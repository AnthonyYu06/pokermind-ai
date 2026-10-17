import {useEffect,useState} from 'react';
import {ArrowRight,FlaskConical,Play,RotateCcw} from 'lucide-react';
import {PokerTable,type State} from './PokerTable';
type Choice={label:string;action:{kind:string;to?:number};probability?:number};
type Match={game_id:string;revision:number;opponent:string;completed:boolean;state:State;net:number|null;artifact_hash:string;model_note:string;choices:Choice[];events:{kind:string;seat:number|null;to:number|null;cards:string[]}[]};
type Policy={revision:number;policy:Choice[];note:string};
type SelfPlay={status:string;runtime_available?:boolean;quality_gates?:Record<string,boolean>;promotion?:{reason:string};runs?:{domain:string;seed:number;episodes:number;metrics:{exploitability?:number;opponents?:Record<string,{bb_per_hand:number;ci95:number[];hands:number}>}}[]};
async function request<T>(path:string,body?:unknown):Promise<T>{
 const r=await fetch('/api'+path,{method:body===undefined?'GET':'POST',headers:{'X-PokerMind-Local':'1','Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});const data=await r.json();if(!r.ok)throw new Error(typeof data.detail==='string'?data.detail:'Invalid action');return data;
}
const chips=(n:number)=>`${(n/100).toLocaleString('en-US',{maximumFractionDigits:2})} chips`;
const name=(value:string)=>value.charAt(0).toUpperCase()+value.slice(1);
const choiceName=(c:Choice)=>name(c.action.kind)+(c.action.to!==undefined?' to '+chips(c.action.to):'');
const opponentNames:Record<string,string>={baseline:'Frequency baseline',research:'Unpromoted GRU',selfplay:'NFSP self-play · research'};
export function HoldemTrainer(){
 const [match,setMatch]=useState<Match|null>(null),[opponent,setOpponent]=useState('baseline'),[amount,setAmount]=useState(0),[available,setAvailable]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const [selfplay,setSelfplay]=useState<SelfPlay|null>(null),[policy,setPolicy]=useState<Policy|null>(null);
 function update(m:Match){setMatch(m);setOpponent(m.opponent);setPolicy(null);const bounds=m.state.legal_actions.raise||m.state.legal_actions.bet;setAmount(bounds?.min_to||0);}
 useEffect(()=>{
  request<{status:string}>('/research/behavior').then(r=>setAvailable(r.status!=='not-run')).catch(e=>setError(e.message));
  request<SelfPlay>('/research/holdem-nfsp').then(setSelfplay).catch(e=>setError(e.message));
  const id=localStorage.getItem('pokermind-holdem-game');if(id)request<Match>('/holdem/'+id).then(update).catch(()=>localStorage.removeItem('pokermind-holdem-game'));
 },[]);
 async function run(body:unknown,path='/holdem'){setBusy(true);setError('');try{const m=await request<Match>(path,body);update(m);localStorage.setItem('pokermind-holdem-game',m.game_id);}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 async function inspect(){if(!match)return;setBusy(true);setError('');try{setPolicy(await request<Policy>(`/holdem/${match.game_id}/policy`,{revision:match.revision}));}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 const legal=match?.state.legal_actions||{},bounds=legal.raise||legal.bet;
 const isSelfplay=match?.opponent==='selfplay';
 const ready=opponent==='selfplay'?selfplay?.runtime_available===true:available;
 const neuralRuns=selfplay?.runs?.filter(r=>r.domain==='holdem')||[];
 return <>
  <div className="page-heading"><div><div className="eyebrow">PRACTICE THE WHOLE HAND</div><h1>A seat against a simulated opponent.</h1><p>Heads-up Hold’em with learned opponents. Every action stays inside this app.</p></div><span className="badge sage">PLAY CHIPS</span></div>
  {error&&<div className="alert error" role="alert">{error}</div>}
  <div className="replay-grid"><section className="panel"><div className="panel-heading"><div><h2>{match?'Your practice table':'Ready when you are'}</h2><p>{match?`0.1 / 0.2 simulated blinds · ${opponentNames[match.opponent]}`:'A complete deal, legal betting, and a saved hand.'}</p></div><span className="badge neutral">SYNTHETIC</span></div>
   {match?<PokerTable state={match.state} playChips/>:<div className="study-empty"><Play size={40}/><h2>Build a rhythm.</h2><p>Choose an opponent and deal your first hand.</p></div>}
   {match?.completed&&<div className="sim-actions"><strong>Hand result: {chips(match.net!)} in simulation</strong><button className="primary" disabled={busy} onClick={()=>run({opponent:match.opponent})}><RotateCcw size={15}/>Deal again</button></div>}
   <div className="replay-foot"><span>{match?.model_note||'Select the self-play research opponent to inspect its mixed strategy.'}</span></div>
  </section><aside><section className="panel simulator-settings"><h2>{match&&!match.completed?'Your action':'Your opponent'}</h2>
   {!match||match.completed?<>
    <label>Model<select aria-label="Holdem opponent" value={opponent} onChange={e=>setOpponent(e.target.value)}><option value="baseline">Frequency baseline</option><option value="research">GRU behavior model · research</option><option value="selfplay">NFSP self-play strategy · research</option></select></label>
    {opponent==='selfplay'&&<p>50 big blinds per player, no rake, fixed bet-size choices. This is an experimental strategy, with no optimality certification.</p>}
    <button className="primary full" disabled={busy||!ready} onClick={()=>run({opponent})}><Play size={15}/>Start a hand</button>
    {!ready&&<p>{opponent==='selfplay'?'Run the local Hold’em self-play workflow to prepare this opponent.':'Run the local behavior training workflow to prepare these opponents.'}</p>}
   </>:isSelfplay?<>
    <p>Choose a move, or let the learned policy play this simulated turn.</p>
    <div className="drill-actions">{match.choices.map(c=><button className="secondary" disabled={busy} key={c.label} onClick={()=>run({kind:c.action.kind,to:c.action.to??null,revision:match.revision},`/holdem/${match.game_id}/action`)}>{choiceName(c)}</button>)}</div>
    <button className="secondary full" disabled={busy} onClick={()=>void inspect()}><FlaskConical size={15}/>Inspect policy mix</button>
    <button className="primary full" disabled={busy} onClick={()=>run({revision:match.revision},`/holdem/${match.game_id}/bot-action`)}><Play size={15}/>Let the bot play this turn</button>
    {policy&&<div className="nfsp-policy" aria-label="Learned action probabilities"><h3>Learned action mix</h3>{policy.policy.map(c=><div key={c.label}><span>{choiceName(c)}</span><strong>{(c.probability!*100).toFixed(1)}%</strong></div>)}<p>{policy.note}</p></div>}
   </>:<>
    {bounds&&<label>Bet / raise to (hundredths of a chip)<input type="number" aria-label="Simulator raise amount" min={bounds.min_to} max={bounds.max_to} value={amount} onChange={e=>setAmount(+e.target.value)}/><small>Legal: {chips(bounds.min_to!)}–{chips(bounds.max_to!)}</small></label>}
    <div className="drill-actions">{Object.entries(legal).map(([kind,b])=><button className="secondary" disabled={busy} key={kind} onClick={()=>run({kind,to:['bet','raise'].includes(kind)?amount:null,revision:match.revision},`/holdem/${match.game_id}/action`)}>{name(kind)}{b.amount?' '+chips(b.amount):''}<ArrowRight size={14}/></button>)}</div>
   </>}
   <p>Your opponent sees its own cards and the public action history. Your private cards stay hidden from it.</p>{match&&<small>Artifact {match.artifact_hash.slice(0,12)}</small>}
  </section>{match&&<section className="panel action-log"><h2>Action history</h2>{match.events.map((e,i)=><p key={i}>{e.kind==='deal'?e.cards.join(' '):`${e.seat===1?'You':'Opponent'} · ${e.kind}${e.to?' to '+chips(e.to):''}`}</p>)}</section>}</aside></div>
  {!!neuralRuns.length&&<section className="panel research-results local-research" aria-label="Self-play research evidence"><div className="panel-heading"><div><h2>The self-play experiment</h2><p>{neuralRuns.reduce((n,r)=>n+r.episodes,0).toLocaleString()} training hands · three seeds · local CPU</p></div><span className="badge amber">UNPROMOTED</span></div><div className="research-body">
   <p>NFSP combines reinforcement learning with a learned average strategy. The river equity networks are frozen inputs; they do not supply optimal-action labels.</p>
   <h3>Held-out simulated opponents</h3><p>BB per hand; 95% intervals use paired deals with seats swapped. These benchmarks do not measure general Hold’em exploitability.</p>
   <div className="table-scroll"><table><thead><tr><th>Seed</th>{Object.keys(neuralRuns[0].metrics.opponents!).map(o=><th key={o}>{name(o.replaceAll('_',' '))}</th>)}</tr></thead><tbody>{neuralRuns.map(r=><tr key={r.seed}><td>{r.seed}</td>{Object.entries(r.metrics.opponents!).map(([o,v])=><td key={o}>{v.bb_per_hand.toFixed(2)}<small>{v.ci95.map(n=>n.toFixed(2)).join(' to ')}</small></td>)}</tr>)}</tbody></table></div>
   <h3>Small-game control</h3>{selfplay?.runs?.filter(r=>r.domain==='kuhn').map(r=><p key={r.seed}>Kuhn seed {r.seed}: exploitability {r.metrics.exploitability?.toFixed(4)} game chips per hand.</p>)}
   <p>{Object.entries(selfplay?.quality_gates||{}).map(([k,v])=>`${k.replaceAll('_',' ')}: ${v?'passed':'not established'}`).join(' · ')}</p><p>{selfplay?.promotion?.reason}</p>
  </div></section>}
 </>;
}
