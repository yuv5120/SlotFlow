import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import Dashboard from "./pages/Dashboard";
import Booking from "./pages/Booking";
import CheckIn from "./pages/CheckIn";
import Login from "./pages/Login";
import Offer from "./pages/Offer";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/book" element={<Booking />} />
        <Route path="/check-in" element={<CheckIn />} />
        <Route path="/login" element={<Login />} />
        <Route path="/offer/:token" element={<Offer />} />
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
);
