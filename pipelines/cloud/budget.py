"""Central, optimistic-concurrency budget reservations, denominated in cents.

Reservations never disappear on TTL/timeout. Uncertain submissions keep their
entire upper bound charged until stop and billing evidence are reconciled. This
is an admission control, not a guarantee that cloud billing cannot exceed a quote.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
import re
from packages.pokermind_core.contracts import digest


def cents(value):
    from decimal import Decimal
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0 or amount * 100 != (amount * 100).to_integral_value():
        raise ValueError('Budget must be nonnegative whole cents')
    return int(amount * 100)


def validate(policy, plan, now):
    if policy.get('cloud_enabled') is not True or cents(policy.get('per_run_budget_usd', 0)) <= 0:
        raise ValueError('Cloud execution is disabled; authorized spending is $0')
    if not policy.get('approved_plan_sha256') or digest(plan) != policy['approved_plan_sha256']:
        raise ValueError('This exact plan has not been approved')
    if plan.get('source_kind') != 'synthetic':
        raise ValueError('Cloud launcher accepts synthetic data only')
    if not plan.get('account_id') or not re.fullmatch(r'\d{12}', plan['account_id']) or plan['account_id'] == '000000000000':
        raise ValueError('Reviewed AWS account required')
    if not 0 < plan['upper_cost_cents'] <= cents(policy['per_run_budget_usd']):
        raise ValueError('Run cost exceeds its approved upper bound')
    if not 0 < plan['timeout_seconds'] <= policy['max_run_minutes'] * 60:
        raise ValueError('Run duration exceeds policy')
    if not 1 <= plan['workers'] <= policy['max_workers'] or plan['gpus'] != 0:
        raise ValueError('Worker/GPU request exceeds approved limits')
    if plan['node_type_id'] not in policy['allowed_instance_types']:
        raise ValueError('Unapproved instance type')
    if not 0 <= plan['max_retries'] <= policy['max_retries']:
        raise ValueError('Retry limit exceeds policy')
    if not 0 < plan['count'] <= policy['max_generated_rows'] or not 0 < plan['max_storage_bytes'] <= policy['max_storage_bytes']:
        raise ValueError('Data/storage bound exceeds policy')
    expires = datetime.fromisoformat(plan['expires_at'].replace('Z', '+00:00'))
    if expires.tzinfo is None or expires.timestamp() <= now + plan['timeout_seconds']:
        raise ValueError('Plan expiry must cover the full run')
    if datetime.fromtimestamp(now, timezone.utc).strftime('%Y-%m') != datetime.fromtimestamp(now + plan['timeout_seconds'], timezone.utc).strftime('%Y-%m'):
        raise ValueError('Run would cross the monthly budget boundary')
    return expires.timestamp()


class Ledger:
    def __init__(self, backend): self.backend = backend

    def reserve(self, policy, plan, token, now):
        expiry = validate(policy, plan, now)
        if not re.fullmatch(r'[a-zA-Z0-9_-]{8,64}', token):
            raise ValueError('Invalid idempotency token')
        month = datetime.fromtimestamp(now, timezone.utc).strftime('%Y-%m')
        key = policy['project'] + ':' + month
        for _ in range(16):
            state, version = self.backend.read(key)
            state = deepcopy(state or {'reservations': {}, 'charged_cents': 0})
            if token in state['reservations']:
                entry = state['reservations'][token]
                if entry['plan_hash'] != digest(plan):
                    raise ValueError('Idempotency token reused for a different plan')
                return {'key': key, 'token': token, **entry}
            active = sum(r['status'] in ('reserved', 'submitted', 'unknown') for r in state['reservations'].values())
            if active >= policy['max_concurrent_jobs'] or len(state['reservations']) >= 512:
                raise ValueError('Concurrency or ledger capacity exhausted')
            if state['charged_cents'] + plan['upper_cost_cents'] > cents(policy['monthly_budget_usd']):
                raise ValueError('Monthly budget exhausted')
            entry = {'plan_hash': digest(plan), 'status': 'reserved', 'upper_cents': plan['upper_cost_cents'], 'expires': expiry,
                     'created': now, 'charged_cents': plan['upper_cost_cents']}
            state['reservations'][token] = entry; state['charged_cents'] += entry['charged_cents']
            if self.backend.compare_and_set(key, version, state):
                return {'key': key, 'token': token, **entry}
        raise RuntimeError('Budget ledger contention; no run submitted')

    def require(self, key, token, plan_hash, now):
        state, _ = self.backend.read(key)
        entry = (state or {}).get('reservations', {}).get(token)
        if not entry or entry['plan_hash'] != plan_hash or entry['expires'] <= now or entry['status'] not in ('reserved', 'submitted', 'unknown'):
            raise ValueError('Missing, expired, closed, or mismatched cloud reservation')
        return entry

    def update(self, key, token, *, status, run_id=None, job_id=None, actual_cents=None, stop_evidence=None, billing_evidence=None):
        if status not in ('reserved', 'submitted', 'unknown', 'closed'):
            raise ValueError('Invalid reservation state')
        if status == 'closed' and (not stop_evidence or not billing_evidence or type(actual_cents) is not int or actual_cents < 0):
            raise ValueError('Closing requires verified stop, billing evidence, and actual cost')
        for _ in range(16):
            state, version = self.backend.read(key); state = deepcopy(state)
            entry = state['reservations'][token]
            if entry['status'] == 'closed':
                if status != 'closed' or entry['charged_cents'] != actual_cents:
                    raise ValueError('Closed reservation cannot be changed')
                return entry
            entry['status'] = status
            if run_id is not None:
                if entry.get('run_id') not in (None, run_id):
                    raise ValueError('Reservation already belongs to another run')
                entry['run_id'] = run_id
            if job_id is not None:
                if entry.get('job_id') not in (None, job_id): raise ValueError('Reservation already belongs to another job')
                entry['job_id'] = job_id
            if status == 'closed':
                state['charged_cents'] += actual_cents - entry['charged_cents']
                entry.update(charged_cents=actual_cents, stop_evidence=stop_evidence, billing_evidence=billing_evidence)
            if self.backend.compare_and_set(key, version, state):
                return entry
        raise RuntimeError('Budget ledger unavailable; reservation retained')


class DynamoBackend:
    def __init__(self, client, table): self.client, self.table = client, table

    def read(self, key):
        result = self.client.get_item(TableName=self.table, Key={'id': {'S': key}}, ConsistentRead=True)
        item = result.get('Item')
        return (json.loads(item['state']['S']), int(item['revision']['N'])) if item else (None, None)

    def compare_and_set(self, key, revision, state):
        args = {'TableName': self.table, 'Item': {'id': {'S': key}, 'revision': {'N': str((revision or 0) + 1)}, 'state': {'S': json.dumps(state)}},
                'ConditionExpression': 'attribute_not_exists(id)' if revision is None else '#r = :r'}
        if revision is not None:
            args.update(ExpressionAttributeNames={'#r': 'revision'}, ExpressionAttributeValues={':r': {'N': str(revision)}})
        try:
            self.client.put_item(**args); return True
        except self.client.exceptions.ConditionalCheckFailedException:
            return False
