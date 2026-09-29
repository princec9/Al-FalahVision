# -*- coding: utf-8 -*-
from odoo import models, fields, api


class ResPartner(models.Model):
    _inherit = 'res.partner'

    c9_customer_id = fields.Char(
        string='Customer ID',
        index=True,
        copy=False,
        help='External customer identifier used to auto-match imported prescriptions '
             '(e.g. "0002", "AA01", "CASH"). Preserves leading zeros and letters exactly '
             'as they appear in the source system - do not store as a number.',
    )
    c9_prescription_ids = fields.One2many(
        'c9.prescription',
        'partner_id',
        string='Prescriptions',
    )
    c9_prescription_count = fields.Integer(
        string='Prescription Count',
        compute='_compute_c9_prescription_count',
    )

    @api.depends('c9_prescription_ids')
    def _compute_c9_prescription_count(self):
        # Count grouped by commercial partner is unnecessary here: prescriptions are
        # matched to the exact partner record whose c9_customer_id matched, so a
        # direct count on this partner is correct.
        counts = self.env['c9.prescription']._read_group(
            [('partner_id', 'in', self.ids)],
            ['partner_id'],
            ['__count'],
        )
        count_map = {partner.id: count for partner, count in counts}
        for rec in self:
            rec.c9_prescription_count = count_map.get(rec.id, 0)

    def action_view_c9_prescriptions(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Prescriptions',
            'res_model': 'c9.prescription',
            'view_mode': 'list,form',
            'domain': [('partner_id', '=', self.id)],
            'context': {'default_partner_id': self.id, 'default_mapping_status': 'matched'},
        }
