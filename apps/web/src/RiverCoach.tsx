import {useEffect,useState} from 'react';
import {ArrowRight,FlaskConical,RotateCcw,Sparkles} from 'lucide-react';
import {PokerTable,type State} from './PokerTable';

type Choice={label:string;action:{kind:string;to?:number}};
type Comparison={version:string;choice:string|null;ranking_source:string;best_actions:string[];reference_best_actions:string[];reference_agrees:boolean;seed_disagreement:boolean;chosen_reference_gap_bb:number|null;
 neural:{status:string;equity?:number;seed_std?:number;reason?:string;member_test_mae_mean?:number;bundle_sha256?:string;training_dataset_id?:string;members?:{seed:number;equity:number;sha256:string}[]};
 reference:{equity:number;combinations:number};actions:(Choice&{reference_ev_bb:number;neural_ev_bb:number|null})[];
 assumptions:string[];ev_convention:string;scope:string;sensitivity:{ranking_changes:boolean;note:string}};
type Drill={drill_id:string;state:State;big_blind:number;source_kind:string;actions:Choice[];result:Comparison|null};
async function request<T>(path:string,body?:unknown):Promise<T>{
 const response=await fetch('/api/coach'+path,{method:body===undefined?'GET':'POST',headers:{'X-PokerMind-Local':'1','Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
 const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Unable to load this practice comparison.');return data;
}
const number=(n:number)=>n.toLocaleString('en-US',{maximumFractionDigits:2});
const percent=(n:number)=>`${(n*100).toFixed(1)}%`;
const title=(choice:Choice)=>choice.action.kind.charAt(0).toUpperCase()+choice.action.kind.slice(1)+(choice.action.to!==undefined?` to ${number(choice.action.to/100)} chips`:'');

export function RiverCoach(){
 const [drill,setDrill]=useState<Drill|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 useEffect(()=>{let active=true;const id=localStorage.getItem('pokermind-coach-drill');if(id){setBusy(true);request<Drill>('/'+id).then(value=>{if(active)setDrill(value);}).catch(()=>localStorage.removeItem('pokermind-coach-drill')).finally(()=>{if(active)setBusy(false);});}return()=>{active=false;};},[]);
 async function run(path:string,body:unknown){setBusy(true);setError('');try{const value=await request<Drill>(path,body);setDrill(value);localStorage.setItem('pokermind-coach-drill',value.drill_id);}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 const result=drill?.result,neural=result?.neural.status==='available';
 const names=(labels:string[])=>labels.map(label=>title(drill!.actions.find(a=>a.label===label)!)).join(' / ');
 return <>
  <div className="page-heading"><div><div className="eyebrow">A PREDICTION YOU CAN EXAMINE</div><h1>Your AI practice coach.</h1><p>Explore a simulated river decision with three trained networks and an exact reference.</p></div><span className="badge sage">SIMULATED CHIPS</span></div>
  {error&&<p className="alert error" role="alert">{error}</p>}
  {!drill?<section className="panel coach-intro"><Sparkles size={34}/><h2>Deal a hand. Compare the choices.</h2><p>Choose an action first, or reveal the comparison. The coach estimates the strongest listed action against a uniform opponent who always checks or calls.</p><button className="primary" disabled={busy} onClick={()=>void run('',{})}><Sparkles size={16}/>{busy?'Opening…':'Deal a river hand'}</button><small>River only · heads-up · zero rake · generated hands</small></section>:<>
   <div className="replay-grid coach-layout"><section className="panel"><div className="panel-heading"><div><h2>Your river decision</h2><p>One big blind = {number(drill.big_blind/100)} simulated chips</p></div><button className="secondary" disabled={busy} onClick={()=>void run('',{})}><RotateCcw size={14}/>New hand</button></div><PokerTable state={drill.state} playChips/><div className="replay-foot"><span>Opponent cards stay hidden from you and the coach.</span></div></section>
   <aside className="panel coach-controls"><span className="overline">{result?'THE ESTIMATED CHOICE':'TRY A DECISION'}</span>
    {result?<><h2>{names(result.best_actions)}</h2><span className={`badge ${neural?'sage':'amber'}`}>{neural?'ML ESTIMATE':'EXACT REFERENCE ONLY'}</span><p>Highest expected value among the listed actions under the assumptions below.</p>{!neural&&<p role="status">{result.neural.reason}</p>}
     {!result.reference_agrees&&<p className="coach-caution">The exact calculation favors {names(result.reference_best_actions)}. The neural estimate changes the ranking.</p>}
     {result.seed_disagreement&&<p className="coach-caution">The three networks disagree about the leading action.</p>}
     {result.sensitivity.ranking_changes&&<p className="coach-caution">The leading action changes when equity shifts by ±5 percentage points. Treat this as a sensitive estimate.</p>}
     {result.choice&&<div className="coach-choice"><small>YOUR CHOICE</small><strong>{names([result.choice])}</strong><p>{result.chosen_reference_gap_bb!<.000001?'Your choice ties for the highest exact value under this model.':`${number(result.chosen_reference_gap_bb!)} BB below the highest exact value under this model.`}</p></div>}
     <button className="primary full" disabled={busy} onClick={()=>void run('',{})}>Next practice hand <ArrowRight size={15}/></button>
    </>:<><h2>What would you choose?</h2><p>Every button is a legal action in this generated hand. Assume a uniform opponent who always checks or calls.</p><div className="drill-actions">{drill.actions.map(action=><button className="secondary" key={action.label} disabled={busy} onClick={()=>void run(`/${drill.drill_id}/analyze`,{action:action.label})}>{title(action)}</button>)}</div><button className="primary full" disabled={busy} onClick={()=>void run(`/${drill.drill_id}/analyze`,{})}><FlaskConical size={15}/>{busy?'Comparing…':'Compare all actions'}</button></>}
   </aside></div>
   {result&&<section className="panel coach-results" aria-label="Model action comparison"><div className="panel-heading"><div><h2>Why the model favors this choice</h2><p>Expected values in big blinds of simulated chips</p></div><span className="badge amber">RESEARCH ESTIMATE</span></div><div className="coach-body">
    <div className="coach-metrics"><div><small>NEURAL SHOWDOWN EQUITY</small><strong>{neural?percent(result.neural.equity!):'Unavailable'}</strong><p>{neural?`Seed spread: ${(result.neural.seed_std!*100).toFixed(2)} pp · not a confidence interval`:'The exact calculation remains available.'}</p></div><div><small>EXACT SHOWDOWN EQUITY</small><strong>{percent(result.reference.equity)}</strong><p>All {result.reference.combinations} legal opponent hands</p></div></div>
    <div className="table-scroll"><table><thead><tr><th>Action</th><th>ML expected value</th><th>Exact expected value</th></tr></thead><tbody>{[...result.actions].sort((a,b)=>(b.neural_ev_bb??b.reference_ev_bb)-(a.neural_ev_bb??a.reference_ev_bb)).map(row=><tr key={row.label} className={result.best_actions.includes(row.label)?'coach-leading':''}><td>{title(row)}{result.best_actions.includes(row.label)&&<small>Best estimate{result.best_actions.length>1?' · tied':''}</small>}</td><td>{row.neural_ev_bb===null?'—':`${number(row.neural_ev_bb)} BB`}</td><td>{number(row.reference_ev_bb)} BB</td></tr>)}</tbody></table></div>
    <p>{result.ev_convention} A positive value can include winning chips already in the pot.</p><h3>The opponent assumptions matter</h3><ul>{result.assumptions.map(text=><li key={text}>{text}</li>)}</ul><p>Large bets can lead here because the simulated opponent never folds or raises. These estimates do not establish the best action against a different opponent.</p><p>{result.sensitivity.note}</p>
    {neural&&<details><summary>Inspect the model evidence</summary><p>The three individual models averaged {(result.neural.member_test_mae_mean!*100).toFixed(2)} percentage points of equity error on the original synthetic test set. That is not an error bound for this hand or a validation of this action ranking.</p>{result.neural.members!.map(member=><p key={member.seed}>Seed {member.seed}: {percent(member.equity)} equity · {member.sha256.slice(0,12)}</p>)}<small>Bundle {result.neural.bundle_sha256?.slice(0,16)} · Dataset {result.neural.training_dataset_id?.slice(0,16)}</small></details>}
    <p className="coach-scope">{result.scope}</p>
   </div></section>}
  </>}
 </>;
}
