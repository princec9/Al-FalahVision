from odoo import models, fields, api
from datetime import datetime
from dateutil.relativedelta import relativedelta

class MonthlySalesReportWizard(models.TransientModel):
    _name = 'sunray.monthly.sales.report.wizard'
    _description = 'Monthly Sales Report Wizard'

    def _get_default_year(self):
        return str(datetime.now().year)

    def _get_default_month(self):
        return str(datetime.now().month)

    month = fields.Selection([
        ('1', 'January'), ('2', 'February'), ('3', 'March'), ('4', 'April'),
        ('5', 'May'), ('6', 'June'), ('7', 'July'), ('8', 'August'),
        ('9', 'September'), ('10', 'October'), ('11', 'November'), ('12', 'December')
    ], string='Month', required=True, default=_get_default_month)
    
    year = fields.Selection([(str(num), str(num)) for num in range(2020, 2031)], string='Year', required=True, default=_get_default_year)
    
    user_ids = fields.Many2many('res.users', string='Store Incharges', help="Select store incharges to generate report for. Leave empty for all.")
    
    def action_view_onscreen(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'sunray_monthly_sales_report.client_action',
            'name': 'Cash Report',
            'context': {
                'month': self.month,
                'year': self.year,
                'user_ids': self.user_ids.ids if self.user_ids else [],
            }
        }
    
    def action_print_report(self):
        self.ensure_one()
        # Calculate dates
        date_start = datetime.strptime(f'{self.year}-{self.month}-01', '%Y-%m-%d').date()
        date_end = date_start + relativedelta(months=1, days=-1)
        
        domain = [('date', '>=', date_start), ('date', '<=', date_end)]
        if self.user_ids:
            domain.append(('salesperson_id', 'in', self.user_ids.ids))
        entries = self.env['sunray.monthly.sales.report.entry'].search(domain, order='salesperson_id, date')
        sales_by_user = {}
        for entry in entries:
            user = entry.salesperson_id
            sales_by_user.setdefault(user, {'orders': [], 'total_amount': 0.0, 'count': 0})
            sales_by_user[user]['orders'].append(entry)
            sales_by_user[user]['total_amount'] += entry.amount
            sales_by_user[user]['count'] += 1

        data = {
            'sales_by_user': sales_by_user,
            'date_start': date_start,
            'date_end': date_end,
            'month_name': dict(self._fields['month'].selection).get(self.month),
            'year': self.year,
            'company': self.env.company,
        }
        
        return self.env.ref('sunray_monthly_sales_report.action_report_monthly_sales').report_action(self, data=data)
