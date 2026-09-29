import re
from odoo import models, fields, api, _
from odoo.osv import expression


class ResPartner(models.Model):
    _inherit = 'res.partner'

    prescription_count = fields.Integer(compute='_compute_prescription_count')

    dob = fields.Date(string='DOB')
    emp_personal = fields.Char(string="Employee Personal ID")
    # is_optometrist_person = fields.Boolean(string="Optometrist")
    # is_technician = fields.Boolean(string="Is Technician")
    # reference_company = fields.Char("Company")
    _rec_names_search = ['complete_name', 'email', 'phone', 'ref', 'vat', 'company_registry',
                         'emp_personal']  # TODO vat must be sanitized the same way for storing/searching

    def _name_search(self, name, domain=None, operator='ilike', limit=None, order=None,
                     active_test=True):
        """Extend partner search so that typing a phone/mobile number (or part
        of it) also matches, in addition to the standard name/email/vat search.

        This affects every many2one/name_search based widget that points to
        res.partner, including Sale Order > Customer (partner_id),
        Invoice > Customer, POS customer selector, etc.
        """
        domain = domain or []

        if name:
            # Build the "normal" search Odoo would have done (name, email, etc.)
            base_domain = self._get_name_search_domain(name, operator)

            # Also try to match against phone/mobile.
            # Strip everything except digits so that formatting
            # (+91, spaces, dashes, parentheses) doesn't break the match.
            digits_only = re.sub(r'\D', '', name)

            phone_domain = []
            if digits_only:
                # Search using both the raw text (in case ilike still works,
                # e.g. numbers stored without formatting) and the digits-only
                # version against phone and mobile.
                phone_domain = expression.OR([
                    [('phone', operator, name)],
                    [('mobile', operator, name)],
                    [('phone', operator, digits_only)],
                    [('mobile', operator, digits_only)],
                ])
            else:
                phone_domain = expression.OR([
                    [('phone', operator, name)],
                    [('mobile', operator, name)],
                ])

            search_domain = expression.OR([base_domain, phone_domain])
            domain = expression.AND([domain, search_domain])

            return self._search(domain, limit=limit, order=order)

        return super()._name_search(name, domain, operator, limit, order)

    def _get_name_search_domain(self, name, operator):
        """Recreate the default name/email/vat/ref search domain that
        Odoo's res.partner._name_search normally builds, so we don't lose
        that behaviour while adding phone/mobile matching.

        Kept as a separate method so it's easy to extend later
        (e.g. add 'ref' or 'vat' fields) without touching the main method.
        """
        return expression.OR([
            [('name', operator, name)],
            [('email', operator, name)],
            [('ref', operator, name)],
            [('vat', operator, name)],
        ])

    @api.depends('name', 'company_id')
    def _compute_prescription_count(self):
        for rec in self:
            rec.prescription_count = self.env['prescription.records'].search_count([('partner_id', '=', rec.id)])

    def action_view_prescriptions(self):
        return {
            'name': _('Prescriptions'),
            'type': 'ir.actions.act_window',
            'res_model': 'prescription.records',
            'view_mode': 'list,form',
            'domain': [('partner_id', '=', self.id)],
            'context': {'create': False, 'edit': False},
        }
