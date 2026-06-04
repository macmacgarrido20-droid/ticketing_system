# MSSCO IT Helpdesk System
Mustard Seed Systems Corporation — Internal IT Ticket System

---

## How to Run

### Step 1 — Set up the database
```
mysql -u root -p < schema.sql
```

### Step 2 — Create your .env file
```
copy .env.example .env
```
Open `.env` and fill in your database password.

### Step 3 — Install Python packages
```
pip install -r requirements.txt
```

### Step 4 — Run the app
```
python app.py
```

### Step 5 — Open in browser
```
http://localhost:5000
```

---

## Default Admin Login

| Field    | Value        |
|----------|--------------|
| Username | admin        |
| Password | Admin@1234   |

Change the password after your first login.

---

## Project Structure

```
mssco_final/
├── app.py               <- Main application
├── schema.sql           <- Run once to set up the database
├── requirements.txt     <- Python packages needed
├── .env.example         <- Copy to .env and fill in values
├── static/
│   ├── style.css        <- All styles
│   └── logo.png         <- MSSCO logo
└── templates/
    ├── base.html            <- Shared navbar and layout
    ├── login.html           <- Login page
    ├── register.html        <- Register page
    ├── my_tickets.html      <- Employee: list of tickets
    ├── new_ticket.html      <- Employee: submit a ticket
    ├── view_ticket.html     <- View ticket, comments, history
    ├── edit_ticket.html     <- Employee: edit an open ticket
    ├── admin_dashboard.html <- Admin: all tickets and stats
    └── manage_users.html    <- Admin: manage accounts
```

---

## Features

- Employee: submit, view, filter, edit their own tickets
- Admin: view all tickets, update status, assign, delete
- Ticket history / audit trail
- File attachments
- Pagination
- User management (activate/deactivate, promote to admin)
