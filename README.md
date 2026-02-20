# Landsky Wedding Application

This repository contains a simple **FastAPI** application that automates
the workflow for handling wedding inquiries for **Landsky Catering**.

Couples (``mladenci``) can submit their wedding details through a form or
by calling the API directly.  The application records the inquiry,
sends a personalised offer by email and waits for the couple to accept
or decline.  If the offer is accepted the event is stored in a
database and a reminder email is scheduled two days before the wedding.

## Features

* **Registration endpoint** (`POST /register`)
  * Accepts first name, last name, wedding date, venue, number of guests,
    email and phone number.
  * Creates a new event in the database and sends an offer email from
    `catering@landskybar.com` to the provided email address.
  * The offer email includes unique links to **accept** or **decline**.

* **Accept endpoint** (`GET /accept?token=...`)
  * Marks the event as accepted, sends confirmation emails to the
    couple and the catering team and schedules a reminder two days
    before the wedding.

* **Decline endpoint** (`GET /decline?token=...`)
  * Removes the pending event if it has not yet been accepted.

* **Reminder emails**
  * A background scheduler (APScheduler) writes reminder jobs into the
    same database so they persist across restarts.
  * Two days before a wedding, an email is sent to
    `catering@landskybar.com` summarising the event.

## Requirements

* Python 3.9 or newer.
* A PostgreSQL database (for example, a **Neon** database).  A local
  SQLite fallback is provided for development if no database URL is
  specified.
* Access to an SMTP server capable of sending emails from
  `catering@landskybar.com`.  During development you can omit
  credentials and emails will be logged to the console instead.

All Python dependencies are listed in `requirements.txt` and can be
installed with `pip`.

```bash
pip install -r requirements.txt
```

## Configuration

The application uses environment variables for configuration.  At a
minimum you will need the following:

| Variable        | Description                                                 | Example                                   |
|-----------------|-------------------------------------------------------------|-------------------------------------------|
| `DATABASE_URL`  | SQLAlchemy connection string to your Neon PostgreSQL DB.    | `postgresql+psycopg2://user:pass@...`     |
| `SMTP_HOST`     | SMTP server hostname.                                       | `smtp.gmail.com`                          |
| `SMTP_PORT`     | SMTP server port (465 for SSL, 587 for TLS).                | `465`                                     |
| `SMTP_USER`     | SMTP username (full email address).                          | `catering@landskybar.com`                 |
| `SMTP_PASSWORD` | SMTP password or app-specific password.                     | `app‑password`                            |
| `BASE_URL`      | Public URL where this API is reachable (for links).         | `https://weddings.landskybar.com`         |

When developing locally you can create a `.env` file in the project
root and load it automatically using [python‑dotenv](https://pypi.org/project/python-dotenv/):

```env
DATABASE_URL=sqlite:///./wedding_app.db
BASE_URL=http://localhost:8000
# Omit SMTP_* to log emails instead of sending
```

If `SMTP_HOST` (and the other SMTP variables) are not set the
application will print the email contents to standard output rather
than sending them.  This is useful for testing without sending real
emails.

## Running the application

After installing dependencies and setting the environment variables, you
can start the server with [Uvicorn](https://www.uvicorn.org/):

```bash
uvicorn wedding_app.main:app --reload
```

The API will be available on <http://localhost:8000/> by default.  Use
an HTTP client like `curl`, [HTTPie](https://httpie.io/) or a browser
to test the endpoints.  For example, to submit a registration:

```bash
http POST http://localhost:8000/register \
    first_name=Ana last_name=Kovač \
    wedding_date=2026-06-15 venue="Hotel Esplanade" \
    guest_count:=120 email=ana@example.com phone="123456789"
```

If the API is reachable from a browser you can embed the form in a
front‑end application and call the same endpoint using JavaScript.

## How it works

1. **Upit (registration)** – The couple provides their details via
   `POST /register`.  The server generates a UUID token, stores the
   event in the database and sends an offer email containing links to
   accept or decline.

2. **Slanje ponude (sending offers)** – The offer email originates
   from `catering@landskybar.com`.  The body describes the event and
   includes two personalised URLs, e.g.:
   * `https://weddings.landskybar.com/accept?token=...`
   * `https://weddings.landskybar.com/decline?token=...`

3. **Prihvaćanje / odbijanje (accept/decline)** – When the couple
   clicks the **accept** link, the server marks the event as accepted,
   sends a confirmation email to the couple and to the catering team
   and schedules a reminder.  If they click **decline**, the pending
   request is removed.

4. **Baza prihvaćenih eventa (accepted events database)** – All
   accepted events remain in the `events` table with the `accepted`
   flag set to true.  You can inspect them with any PostgreSQL client.

5. **Podsjetnik (reminder)** – Two days prior to the wedding the
   background scheduler triggers a job that sends an internal email to
   `catering@landskybar.com` summarising the event.  The scheduler
   stores job metadata in the same database so reminders persist across
   restarts.

## Development notes

* The application uses **SQLAlchemy** to abstract the database layer.
  A SQLite database (`wedding_app.db`) is automatically created if
  `DATABASE_URL` is not set.  When connecting to a Neon database you
  must supply a full Postgres URL.
* **APScheduler** is used to schedule reminder emails.  Jobs are
  stored in the database to survive restarts.  Should you wish to use
  a separate job store (e.g. Redis) you can adjust the configuration in
  `main.py`.
* Emails are sent via SMTP using Python’s built‑in `smtplib`.  For
  production you might prefer a dedicated email service (SendGrid,
  Mailgun, etc.).  To support such services replace the `send_email`
  function in `main.py`.
* Timezone handling uses Python’s `zoneinfo` module.  The default is
  Europe/Zagreb; if unavailable the system falls back to naive
  datetimes.

## License

This project is provided under the MIT License.  You are free to use,
modify and distribute it as needed.