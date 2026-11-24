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
LOAN_RULE_CODE = 'LO'


def _create_loan_rule(env, structure):
    Rule = env['hr.salary.rule']
    # Enterprise payroll stores the structure on the salary rule, while the
    # community payroll module stores rules on structure.rule_ids.
    if 'struct_id' in Rule._fields:
        existing_rule = Rule.search_count([
            ('struct_id', '=', structure.id), ('code', '=', LOAN_RULE_CODE),
        ])
    else:
        existing_rule = structure.rule_ids.filtered(
            lambda rule: rule.code == LOAN_RULE_CODE
        )
    if existing_rule:
        return
    category = env.ref('hr_payroll_community.DED', raise_if_not_found=False)
    if not category:
        category = env.ref('hr_payroll.DED', raise_if_not_found=False)
    if not category:
        # fall back to any deduction category by code
        category = env['hr.salary.rule.category'].search(
            [('code', '=', 'DED')], limit=1)
    if not category:
        category = env['hr.salary.rule.category'].create(
            {'name': 'Deduction', 'code': 'DED'})
    rule_vals = {
        'name': 'Loan',
        'code': LOAN_RULE_CODE,
        'category_id': category.id,
        'sequence': 190,
        'amount_select': 'code',
        'amount_python_compute': 'result = -payslip.loan_deduction',
        'appears_on_payslip': True,
    }
    if 'struct_id' in Rule._fields:
        rule_vals['struct_id'] = structure.id
    rule = Rule.create(rule_vals)
    if 'struct_id' not in Rule._fields:
        structure.write({'rule_ids': [(4, rule.id)]})


def post_init_hook(env):
    """Add the Loan deduction rule to every existing salary structure."""
    for structure in env['hr.payroll.structure'].search([]):
        _create_loan_rule(env, structure)
