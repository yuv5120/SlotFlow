"use client";
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Check, HeartPulse, Search } from "lucide-react";
import { DateTime } from "luxon";
import type { Appointment } from "../types";
export default function CheckIn(){
  const [appointments,setAppointments]=useState<Appointment[]>([]),[zone,setZone]=useState("Asia/Kolkata"),[query,setQuery]=useState(""),[notice,setNotice]=useState("");
  const refresh=useCallback(()=>fetch("/api/bootstrap",{cache:"no-store"}).then(r=>{if(r.status===401){window.location.href="/login";throw new Error("Sign-in required");}return r.json();}).then(d=>{setAppointments(d.appointments||[]);setZone(d.clinic?.timezone||"Asia/Kolkata");}).catch(()=>{}),[]);
  useEffect(()=>{refresh();},[refresh]);
  const today=DateTime.now().setZone(zone).toISODate();
  const shown=appointments.filter(a=>DateTime.fromISO(a.startsAt).setZone(zone).toISODate()===today&&a.status!=="cancelled"&&(a.patient+a.phone).toLowerCase().includes(query.toLowerCase())).sort((a,b)=>a.startsAt.localeCompare(b.startsAt));
  async function checkIn(id:string){const r=await fetch(`/api/appointments/${id}`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({status:"checked_in"})});if(r.ok){setNotice("Patient checked in");refresh();}else setNotice((await r.json()).error||"Could not check in");}
  return <main className="checkin-shell"><div className="checkin-wrap"><div className="checkin-top"><Link to="/" className="brand"><span className="brand-mark"><HeartPulse size={22}/></span><span>slot<span className="brand-light">flow</span><i>.</i></span></Link><Link to="/">← Back to dashboard</Link></div><div className="eyebrow">FRONT DESK / {DateTime.now().setZone(zone).toFormat("LLL d, yyyy").toUpperCase()}</div><h1>Patient check-in<span className="heading-dot">.</span></h1><p>Find a patient by name or phone number, then mark them as arrived.</p><div className="checkin-search"><Search size={18}/><input placeholder="Search patient or phone number" value={query} onChange={e=>setQuery(e.target.value)}/></div>{notice&&<div className="booking-message">{notice}</div>}{shown.map(a=><div className="checkin-row" key={a.id}><div className="person-avatar" style={{background:"#e8e1f8"}}>{a.patient.split(" ").map(x=>x[0]).join("")}</div><div><strong>{a.patient}</strong><small>{DateTime.fromISO(a.startsAt).setZone(zone).toFormat("h:mm a")} · {a.service} · {a.staff}</small></div><span className={`status ${a.status}`}>{a.status.replace("_"," ")}</span>{a.status==="confirmed"&&<button className="primary-btn" onClick={()=>checkIn(a.id)}><Check size={15}/> Check in</button>}</div>)}{!shown.length&&<div className="booking-card empty-state">No patients found for today.</div>}</div></main>;
}
