# HR Intercompany Salary Allocation

**Odoo 19 Enterprise Module**  
Version: `19.0.1.0.0`  
Category: Human Resources / Payroll

---

## Overview

This module enables automatic **multi-company salary cost allocation** and
**intercompany invoice generation** for employees who work across multiple
companies in an Odoo multi-company setup.

---

## Architecture

```
bt_hr_intercompany_salary/
├── __manifest__.py
├── __init__.py
├── models/
│   ├── __init__.py
│   ├── employee_company_contribution.py   # Core allocation model
│   ├── hr_employee.py                     # Employee extension
│   ├── hr_payslip.py                      # Invoice generation logic
│   ├── account_move.py                    # Traceability fields
│   └── res_config_settings.py             # Configuration
├── views/
│   ├── employee_company_contribution_views.xml
│   ├── hr_employee_views.xml
│   ├── hr_payslip_views.xml
│   ├── account_move_views.xml
│   ├── res_config_settings_views.xml
│   └── bt_hr_intercompany_salary_menus.xml
├── security/
│   ├── ir.model.access.csv
│   └── bt_hr_intercompany_salary_security.xml
├── data/
│   └── bt_hr_intercompany_salary_data.xml
├── report/
│   ├── hr_intercompany_contribution_report.xml
│   └── hr_intercompany_reconciliation_report.xml
└── wizard/
    ├── __init__.py
    ├── hr_payslip_reprocess_wizard.py
    └── hr_payslip_reprocess_wizard_views.xml
```

---

## Installation

1. Copy the module to your Odoo addons path.
2. Update the app list.
3. Install **HR Intercompany Salary Allocation**.

**Dependencies:** `hr_payroll`, `account`, `account_accountant`, `base_setup`

---

## Configuration

### Step 1 — Enable the Feature
Go to **Payroll → Configuration → Settings → Intercompany Salary Allocation**:

| Setting | Description |
|---|---|
| Enable Intercompany Salary Allocation | Master switch for the feature |
| Auto-Create Vendor Bills | Creates corresponding vendor bill in each target company |
| Strict Percentage Validation | Enforces total = 100% on save |
| Default Income Account | Fallback income account for customer invoices |
| Default Expense Account | Fallback expense account for vendor bills |

> Configure default accounts **per company** by switching the active company before saving.

### Step 2 — Configure Employees
On each multi-company employee:

1. Navigate to the **Salary Allocation** tab.
2. Check **Multi-Company Employee**.
3. Add rows for each company (including or excluding the parent company):

| Company | Contribution % | Effective From | Effective Until |
|---|---|---|---|
| Parent Corp | 60% | | |
| Subsidiary A | 25% | 2025-01-01 | |
| Subsidiary B | 15% | | |

> Total must equal **100%** (unless strict validation is disabled).

---

## How It Works

### Journal Entry Generation Flow

```
Payslip Confirmed (action_payslip_done)
        │
        ▼
Is employee multi-company? ──No──► Skip
        │ Yes
        ▼
Get active allocation lines (excludes parent company's own line)
        │
        ▼
For each external allocation line:
  ┌───────────────────────────────────────────────┐
  │  amount = salary × pct / 100                   │
  │                                                │
  │  Source entry (paying company) — DRAFT         │
  │  → Dr Intercompany Receivable (target partner) │
  │  → Cr Income                                   │
  │  → General journal                             │
  │                                                │
  │  Counterpart entry (receiving company) — DRAFT │
  │  → Dr Expense                                  │
  │  → Cr Intercompany Payable (source partner)    │
  │  → General journal                             │
  └───────────────────────────────────────────────┘
        │
        ▼
Post chatter note on payslip + each entry (origin/audit trail)
Link source entries to payslip (Many2many)
```

> **Entries are created in Draft and are never auto-posted.** Posting is a
> deliberate, reviewed step. Every entry carries a chatter note recording the
> source payslip, employee, period, paying/receiving company and amount.

### Cancellation Flow

```
Payslip Cancelled / Reset to Draft
        │
        ▼
Collect ALL related entries (source + counterpart) via
intercompany_payslip_id  (sudo, cross-company safe)
        │
        ▼
For each entry:
  draft  → Cancel (state = Cancelled, kept for audit)
  posted → Create reversal + post it
        │
        ▼
Post chatter note on payslip + each entry recording the cancellation
```

---

## Edge Cases Handled

| Scenario | Handling |
|---|---|
| Employee leaves mid-period | Use `date_end` on contribution line |
| Contribution % changes | Use `date_start`/`date_end` to version contributions |
| Multiple payslips in same month | Duplicate guard checks existing active invoices |
| Currency differences | Each invoice is created in the company's default currency |
| Feature disabled mid-cycle | Reprocess Wizard regenerates invoices for past payslips |
| Payslip cancelled after generation | Both the source and counterpart entries are cancelled (draft) or reversed (posted) |

---

## Account Resolution Priority

For **income account** (customer invoice lines):
1. `employee.company.contribution.income_account_id`
2. `ir.config_parameter: bt_hr_intercompany_salary.default_income_account_{company_id}`
3. Auto-search: first active income account in the company

For **expense account** (vendor bill lines):
1. `employee.company.contribution.expense_account_id`
2. `ir.config_parameter: bt_hr_intercompany_salary.default_expense_account_{company_id}`
3. Auto-search: first active expense account in the company

---

## Reports

| Report | Location |
|---|---|
| Contribution Summary (PDF) | Employee → Print → Contribution Summary |
| Contribution Pivot/Graph | Payroll → Intercompany Allocation → Contribution Summary |
| Reconciliation Report (PDF) | Payroll → Intercompany Allocation → Reconciliation Report |
| Intercompany Invoices | Payroll → Intercompany Allocation → Reconciliation Report (list) |

---

## Reprocess Wizard

If the feature was enabled after payslips were already confirmed, use:
**Payroll → Intercompany Allocation → Reprocess Invoices**

Select date range, company, and optional employee filter, then click **Generate Invoices**.

---

## Security

| Group | Access |
|---|---|
| HR User | Read contributions |
| HR Manager | Full CRUD on contributions |
| Payroll User | Read contributions |
| Payroll Manager | Full CRUD + reprocess wizard |

Multi-company record rules ensure users only see contribution records
for companies they belong to. HR Managers bypass this restriction.

---

## Technical Notes

- All invoice creation uses `with_company(company)` for proper multi-company context.
- Vendor bill creation uses `sudo()` to allow cross-company record creation.
- All operations are wrapped in `savepoint()` for safe rollback on failure.
- Duplicate invoice guard checks for non-cancelled invoices before generating.
- Full logging at INFO/WARNING/ERROR levels for auditability.
