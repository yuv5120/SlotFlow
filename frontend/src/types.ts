export type AppointmentStatus = "confirmed" | "checked_in" | "completed" | "cancelled" | "no_show";
export type Appointment = {
  id: string; patient: string; phone: string; service: string; staff: string;
  staffId: string; serviceId: string; startsAt: string; endsAt: string;
  status: AppointmentStatus; risk: "low" | "medium" | "high"; source: string;
};
export type WaitEntry = { id: string; patient: string; phone: string; service: string; serviceId: string; staffId: string; preferredDate: string; status: "waiting" | "offered" | "booked" | "expired"; createdAt: string };
