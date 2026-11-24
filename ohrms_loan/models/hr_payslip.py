# -*- coding: utf-8 -*-
#############################################################################
#    A part of Open HRMS Project <https://www.openhrms.com>
#
#    Cybrosys Technologies Pvt. Ltd.
#
#    Copyright (C) 2026-TODAY Cybrosys Technologies(<https://www.cybrosys.com>)
#    Author: Cybrosys Techno Solutions(<https://www.cybrosys.com>)
#
#    You can modify it under the terms of the GNU LESSER
#    GENERAL PUBLIC LICENSE (LGPL v3), Version 3.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU LESSER GENERAL PUBLIC LICENSE (LGPL v3) for more details.
#
#    You should have received a copy of the GNU LESSER GENERAL PUBLIC LICENSE
#    (LGPL v3) along with this program.
#    If not, see <http://www.gnu.org/licenses/>.
#
#############################################################################
from odoo import api, fields, models


class HrPayslip(models.Model):
    """Extends the Enterprise 'hr.payslip' to deduct approved loan
    installments that fall inside the payslip period."""
    _inherit = 'hr.payslip'

    loan_line_ids = fields.One2many(
        'hr.loan.line', 'payslip_id', string="Loan Installments",
        help="Loan installments deducted in this payslip.")
    loan_deduction = fields.Float(
        string="Loan Deduction", compute='_compute_loan_deduction',
        help="Total loan installments deducted in this payslip. "
             "Used by the 'Loan' salary rule (code LO).")

    @api.depends('loan_line_ids.amount')
    def _compute_loan_deduction(self):
        for slip in self:
            slip.loan_deduction = sum(slip.loan_line_ids.mapped('amount'))

    def _link_loan_lines(self):
        """Attach the unpaid installments due in the payslip period."""
        for slip in self:
            if slip.state in ('validated', 'paid', 'cancel'):
                continue
            # release lines linked by an earlier computation
            slip.loan_line_ids.filtered(lambda l: not l.paid).write(
                {'payslip_id': False})
            if not (slip.employee_id and slip.date_from and slip.date_to):
                continue
            lines = self.env['hr.loan.line'].search([
                ('loan_id.employee_id', '=', slip.employee_id.id),
                ('loan_id.state', '=', 'approve'),
                ('paid', '=', False),
                ('date', '>=', slip.date_from),
                ('date', '<=', slip.date_to),
            ])
            lines.write({'payslip_id': slip.id})

    def compute_sheet(self):
        self._link_loan_lines()
        return super().compute_sheet()

    def action_payslip_done(self):
        """Mark the linked installments as paid and refresh the loan."""
        res = super().action_payslip_done()
        for slip in self:
            lines = slip.loan_line_ids.filtered(lambda l: not l.paid)
            if lines:
                lines.write({'paid': True})
                lines.mapped('loan_id')._compute_total_amount()
        return res
