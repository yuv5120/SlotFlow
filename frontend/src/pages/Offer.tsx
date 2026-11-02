"use client";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { Link } from "react-router-dom";
export default function OfferPage(){
  const {token}=useParams<{token:string}>(); const [status,setStatus]=useState<"idle"|"loading"|"done"|"error">("idle");const [message,setMessage]=useState("");
  async function confirm(){setStatus("loading");const r=await fetch(`/api/offer/${token}`,{method:"POST"});const data=await r.json();setStatus(r.ok?"done":"error");setMessage(r.ok?"Your appointment is confirmed.":data.error);}
  return <main className="booking-shell"><div className="booking-card offer-card"><div className="brand-icon">✳</div><h1>Good news, a time opened up.</h1><p>Your waitlist offer is held for 15 minutes. Confirm now to claim it.</p>{status==="done"||status==="error"?<div className="booking-message">{message}</div>:<button className="primary-btn" onClick={confirm} disabled={status==="loading"}>{status==="loading"?"Confirming…":"Confirm appointment"}</button>}<Link to="/book" className="subtle-link">Back to booking</Link></div></main>;
}
