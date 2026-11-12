import re
from odoo import models, fields, api, _
from odoo.fields import Domain


class ResPartner(models.Model):
    _inherit = 'res.partner'

    prescription_count = fields.Integer(compute='_compute_prescription_count')
    emarates_id = fields.Char(string="Emarates ID")

    dob = fields.Date(string='DOB')
    emp_personal = fields.Char(string="Employee Personal ID")
    is_optometrist_person = fields.Boolean(string="Optometrist")
    is_technician = fields.Boolean(string="Is Technician")
    # reference_company = fields.Char("Company")
    phone_digits = fields.Char(
        string="Phone (digits only)",
        compute='_compute_phone_digits',
        store=True,
        index='trigram',
    )
    _rec_names_search = ['complete_name', 'email', 'phone', 'ref', 'vat', 'company_registry',
                         'emp_personal', 'phone_digits']  # TODO vat must be sanitized the same way for storing/searching

    @api.depends('phone')
    def _compute_phone_digits(self):
        for rec in self:
            rec.phone_digits = re.sub(r'\D', '', rec.phone or '')

    def _search_display_name(self, operator, value):
        domain = super()._search_display_name(operator, value)
        if operator in ('ilike', 'like', '=ilike') and value:
            # only treat the text as a phone number if it looks like one
            if re.fullmatch(r'[\d\s+\-().]+', value.strip()):
                digits = re.sub(r'\D', '', value)
                if len(digits) >= 3:
                    domain = Domain(domain) | Domain('phone_digits', 'ilike', digits)
        return domain

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


class ResCompany(models.Model):
    _inherit = "res.company"

    facility_number = fields.Char(string="DHA Facility Number")


class CrmLead(models.Model):
    _inherit = 'crm.lead'

    def _prepare_opportunity_quotation_context(self):
        ctx = super()._prepare_opportunity_quotation_context()
        partner = self.user_id.partner_id
        if partner:
            ctx['default_salesperson_partner_id'] = partner.id
        return ctx
