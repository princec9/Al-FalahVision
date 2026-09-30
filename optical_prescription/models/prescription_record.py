from odoo import fields, api, models, _
from odoo.exceptions import UserError, ValidationError


class CommissionMixin(models.Model):
    _name = "prescription.records"
    _rec_name = "display_name"
    previous_prescription_count = fields.Integer(
        compute="_compute_previous_prescription_count"
    )
    license_key = fields.Char(
        string='License ',
        related='optometrist_id.license_key',
        readonly=True,
    )

    def _compute_previous_prescription_count(self):
        for rec in self:
            rec.previous_prescription_count = self.search_count([
                ('partner_id', '=', rec.partner_id.id),
                ('id', '!=', rec.id),
            ])

    def action_view_previous_prescriptions(self):
        self.ensure_one()

        return {
            'name': _('Previous Prescriptions'),
            'type': 'ir.actions.act_window',
            'res_model': 'prescription.records',
            'view_mode': 'list,form',
            'domain': [
                ('partner_id', '=', self.partner_id.id),
            ],
            'context': {
                'create': False,
            },
        }

    visit_purpose_note = fields.Text(
        string='Visit Purpose ',
    )
    prescribed_by = fields.Selection(
        selection=[
            ('hospital', 'Hospital'),
            ('self', 'Customer Preference'),
            ('old', 'Old'),
            ('alfalah', 'AlFalah'),
        ],
        # required=True,
        string='Prescribed By',
    )

    referred_by = fields.Char(
        string='Referred By',
    )

    hospital_name = fields.Char(
        string='Hospital Name',
    )

    optometrist_partner_id = fields.Many2one(
        comodel_name='res.partner',
        string='Optometrist',
        # domain="[('is_optometrist_person', '=', True)]",
    )
    
    optometrist_id = fields.Many2one(
        comodel_name='hr.employee',
        string='Optometrist',
        domain="[('is_optometrist', '=', True)]", 
        # required=True
    )
    display_name = fields.Char(compute="_compute_display_name", store=True)
    state = fields.Selection([('draft', 'Draft'),
                              ('sent', 'Sent'),
                              ('sale', 'Sale'),
                              ('cancel', 'Cancel')])

    @api.depends('order_id')
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = f"{rec.order_id.name} - Prescription"

    new_rsph = fields.Many2one('sph.cyl')
    new_rcyl = fields.Many2one('sph.cyl')
    new_raxis = fields.Char()
    new_radd = fields.Many2one('power.add')
    new_rpd = fields.Char()

    new_lsph = fields.Many2one('sph.cyl')
    new_lcyl = fields.Many2one('sph.cyl')
    new_laxis = fields.Char()
    new_ladd = fields.Many2one('power.add')
    new_lpd = fields.Char()

    frame_shape = fields.Char('Shape')
    frame_type = fields.Char('Frame Type')
    frame_length = fields.Float('A size')
    frame_height = fields.Float('B size')
    frame_dbl = fields.Char('DBL')
    frame_diameter = fields.Char('Diameter')
    frame_thickness_type = fields.Char('Thickness Type')
    frame_pd_height_r = fields.Char('Right PD')
    frame_pd_height_l = fields.Char('Left PD')
    frame_segment_height = fields.Char('FH‑SH')
    frame_ipd = fields.Char('IPD')
    frame_index = fields.Char('Index')

    product_tmpl_id = fields.Many2one('product.template')
    res_model = fields.Char('Resource Model')
    line_res_id = fields.Many2oneReference('Resource ID', model_field='res_model')

    res_id = fields.Many2one("sale.order.line")
    order_id = fields.Many2one('sale.order')
    partner_id = fields.Many2one('res.partner')
    fitting_number = fields.Integer()
    fitting_type_id = fields.Many2one('fitting.type')
    note = fields.Char(string="Note")

    # @api.onchange('new_raxis', 'new_laxis')
    # def _check_age_digits(self):
    #     """ANAND MC JUL 2 2025- check axis
    #     (calculated as degrees so in between 0 to 180)"""
    #     for rec in self:
    #         if rec.new_raxis and (rec.new_raxis < 0 or rec.new_raxis > 180):
    #             raise ValidationError("RE Axis must be a 3-digit number (0–180).")
    #         if rec.new_laxis and (rec.new_laxis < 0 or rec.new_laxis > 180):
    #             raise ValidationError("LE Axis must be a 3-digit number (0–180).")

    def update_res_id_line(self):
        record = self.env[self.res_model].search([('id', '=', int(self.line_res_id))])
        if record and self.res_model == 'sale.order':
            record.update_prescription(self.id)
        elif record and self.res_model == 'account.move':
            record.update_prescription(self.id)
