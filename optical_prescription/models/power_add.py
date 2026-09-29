from odoo import fields, api, models
from odoo.exceptions import UserError, ValidationError


class AddPower(models.Model):
    _name = 'power.add'
    _order = 'power'

    @api.depends('power')
    def _compute_display_name(self):
        for record in self:
            name = "{:.2f}".format(record.power)
            if record.power > 0:
                name = '+' + str(name)
            record.display_name = name

    name = fields.Char()
    power = fields.Float(string="Power", default=0,
                         compute="_compute_name", store=True)

    @api.onchange('name')
    def onchange_name(self):
        if self.name:
            try:
                float(self.name)
            except ValueError:
                raise UserError(_("Must Enter A Float Value in Power Field"))

    @api.depends('name')
    def _compute_name(self):
        for rec in self:
            rec.power = float(rec.name)
