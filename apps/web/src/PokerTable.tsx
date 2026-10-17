import {Spade} from 'lucide-react';
const money=(n:number)=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(n/100);
export type Seat = {seat:number;stack:number;bet:number;contributed:number;folded:boolean;cards:string[];button:boolean;hero:boolean;refund:number;award:number};
export type Bounds = {min_to?:number;max_to?:number;amount?:number};
export type State = {street:string;board:string[];actor:number|null;pot:number;settled:boolean;legal_actions:Record<string,Bounds>;seats:Seat[]};
export function Card({value,small=false}:{value?:string;small?:boolean}) {
  const suits:Record<string,string>={s:'♠',h:'♥',d:'♦',c:'♣'};
  return <span className={`card ${small?'small':''} ${!value?'hidden-card':''} ${value && 'hd'.includes(value[1])?'red':''}`} aria-label={value || 'Hidden card'}>{value ? <><b>{value[0]==='T'?'10':value[0]}</b><span>{suits[value[1]]}</span></>:<Spade size={small?13:20}/>}</span>;
}
export function PokerTable({state,compact=false,playChips=false}:{state:State;compact?:boolean;playChips?:boolean}) {
  const amount=(n:number)=>playChips?`${(n/100).toLocaleString('en-US',{maximumFractionDigits:2})} chips`:money(n);
  return <div className={`table-stage ${compact?'compact':''}`}><div className="felt"><span className="felt-label">POKERMIND <Spade size={12}/></span><div className="community">{Array.from({length:5},(_,i)=><Card key={i} value={state.board[i]}/>)}</div><div className="pot-label">{state.settled?'Settled pot':'Pot'} <strong>{amount(state.pot)}</strong></div></div>
    {state.seats.map((s,i)=>{const angle=(i/state.seats.length)*Math.PI*2+Math.PI/2;return <div key={s.seat} className={`seat ${s.folded?'folded':''} ${state.actor===s.seat?'acting':''} ${s.hero?'hero-seat':''}`} style={{left:`${50+41*Math.cos(angle)}%`,top:`${49+39*Math.sin(angle)}%`}}><div className="hole-cards"><Card small value={s.cards[0]}/><Card small value={s.cards[1]}/></div><div className="seat-name">{s.hero?'You':`Seat ${s.seat}`}{s.button&&<span className="dealer">D</span>}</div><strong>{amount(s.stack)}</strong>{s.folded?<span className="seat-note">Folded</span>:s.bet>0&&!state.settled?<span className="seat-note">In front {amount(s.bet)}</span>:s.award>0?<span className="seat-note">Award {amount(s.award)}</span>:null}</div>;})}
  </div>;
}
