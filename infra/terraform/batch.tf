# Separate from serving: only an approved batch job role may mutate synthetic data.
resource "aws_dynamodb_table" "budget" {
  count        = var.enabled ? 1 : 0
  name         = "pokermind-budget-ledger"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"
  attribute {
    name = "id"
    type = "S"
  }
  server_side_encryption { enabled = true }
  point_in_time_recovery { enabled = true }
  deletion_protection_enabled = true
  lifecycle { prevent_destroy = true }
  # Deliberately no TTL: an expired run must never refund an uncertain charge.
}
resource "aws_iam_role" "batch" {
  count = var.enabled ? 1 : 0
  name  = "pokermind-synthetic-batch"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole"
  }] })
}
resource "aws_iam_instance_profile" "batch" {
  count = var.enabled ? 1 : 0
  name  = "pokermind-synthetic-batch"
  role  = aws_iam_role.batch[0].name
}
resource "aws_iam_role_policy" "batch" {
  count = var.enabled ? 1 : 0
  role  = aws_iam_role.batch[0].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject"], Resource = [
      "${aws_s3_bucket.data["lake"].arn}/pokermind/synthetic/*", "${aws_s3_bucket.data["published"].arn}/snapshots/pokermind/*"
    ] },
    # Spark's S3 commit cleanup requires deletion of task temporary objects.
    # Serving snapshots and all personal-data prefixes remain outside this grant.
    { Effect = "Allow", Action = ["s3:DeleteObject", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"], Resource = [
      "${aws_s3_bucket.data["lake"].arn}/pokermind/synthetic/attempts/*", "${aws_s3_bucket.data["lake"].arn}/pokermind/synthetic/scores/*"
    ] },
    { Effect = "Allow", Action = ["s3:ListBucket", "s3:ListBucketVersions"], Resource = aws_s3_bucket.data["lake"].arn,
    Condition = { StringLike = { "s3:prefix" = ["pokermind/synthetic/*"] } } },
    { Effect = "Allow", Action = ["s3:ListBucket", "s3:ListBucketVersions"], Resource = aws_s3_bucket.data["published"].arn,
    Condition = { StringLike = { "s3:prefix" = ["snapshots/pokermind/*"] } } },
    { Effect = "Allow", Action = ["dynamodb:GetItem"], Resource = aws_dynamodb_table.budget[0].arn }
  ] })
}
# Attach to a reviewed launcher identity separately. Job workers cannot reserve
# or refund money; they only read the launcher's central reservation.
resource "aws_iam_policy" "budget_launcher" {
  count = var.enabled ? 1 : 0
  name  = "pokermind-budget-launcher"
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:PutItem"], Resource = aws_dynamodb_table.budget[0].arn
  }] })
}
output "batch_instance_profile_arn" { value = try(aws_iam_instance_profile.batch[0].arn, null) }
output "budget_launcher_policy_arn" { value = try(aws_iam_policy.budget_launcher[0].arn, null) }
