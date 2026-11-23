// The clinic's scheduling book and the clinic's journal, both append-only.
//
// Append-only on purpose: the journal is the clinic's side of the evidence
// pair. One entry says the gate released data to this code; the other says the
// clinic's own system accepted a recall from this code. A file somebody can
// rewrite is not that.
import { appendFileSync, existsSync, mkdirSync, readFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { dirname, join } from 'node:path';

const SLOT_HOURS = ['08:00', '08:40', '09:20', '10:00', '10:40', '11:20', '13:00', '13:40', '14:20', '15:00'];

export class ClinicStore {
  constructor(directory) {
    this.directory = directory;
    this.appointmentsPath = join(directory, 'appointments.jsonl');
    this.journalPath = join(directory, 'journal.jsonl');
    if (!existsSync(directory)) mkdirSync(directory, { recursive: true });
  }

  #read(path) {
    if (!existsSync(path)) return [];
    return readFileSync(path, 'utf8')
      .split('\n')
      .filter((line) => line.trim() !== '')
      .map((line) => JSON.parse(line));
  }

  #append(path, record) {
    if (!existsSync(dirname(path))) mkdirSync(dirname(path), { recursive: true });
    appendFileSync(path, `${JSON.stringify(record)}\n`, 'utf8');
    return record;
  }

  appointments() {
    return this.#read(this.appointmentsPath);
  }

  journal() {
    return this.#read(this.journalPath);
  }

  /** What the journal records about one call, accepted or refused. */
  record(entry) {
    return this.#append(this.journalPath, { at: new Date().toISOString(), ...entry });
  }

  /**
   * Book a recall. Idempotent on `recall_id`: the same recall booked twice
   * returns the first appointment rather than a second slot, which is what
   * keeps a repeating signal from filling the diary.
   */
  book(request, caller) {
    const existing = this.appointments().find((row) => row.recall_id === request.recall_id);
    if (existing) return { appointment: existing, outcome: 'already_booked' };

    const booked = this.appointments().length;
    const date = new Date(Date.now() + (7 + (booked % 21)) * 86_400_000).toISOString().slice(0, 10);
    const appointment = {
      appointment_id: `APPT-${randomUUID().slice(0, 8).toUpperCase()}`,
      booked_at: new Date().toISOString(),
      recall_id: request.recall_id,
      mrn: request.mrn,
      patient_name: request.patient_name,
      site: request.site,
      procedure: 'Surveillance colonoscopy',
      appointment_date: date,
      appointment_time: SLOT_HOURS[booked % SLOT_HOURS.length],
      outreach_channel: request.outreach_channel,
      due_date: request.due_date ?? null,
      days_overdue: request.days_overdue ?? null,
      reason: request.reason,
      requested_by: request.requested_by,
      signal_url: request.signal_url ?? null,
      accepted_from: {
        hostname: caller.hostname,
        evidence_digest: caller.evidenceDigest,
        cert_fingerprint: caller.certFingerprint,
        attested_resources: caller.resources,
      },
    };
    return { appointment: this.#append(this.appointmentsPath, appointment), outcome: 'booked' };
  }
}
