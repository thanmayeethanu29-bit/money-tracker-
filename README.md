# Pocketjar — Money Tracker Application (Python + MySQL)

Web version of the Money Tracker: users register, log in and see **only their own** data.

## Features
- Registration/login with hashed passwords
- Add, edit, delete, search transactions (income and expense, payment mode)
- Custom categories, monthly budgets per category with alerts at 80%+
- SQL aggregate reports (SUM, GROUP BY) and a category chart
- CSV export

## Database (3NF)
`users`, `categories`, `transactions`, `budgets` — see `schema.sql` (foreign keys, index on `user_id, txn_date`).

## Setup
1. `pip install -r requirements.txt`
2. `mysql -u root -p < schema.sql`
3. Copy `.env.example` to `.env`, add your MySQL password
4. `python app.py` → http://127.0.0.1:5000
