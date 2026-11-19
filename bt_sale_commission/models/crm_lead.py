from odoo import api, fields, models


class CrmLead(models.Model):
    _inherit = "crm.lead"

    emarates_id = fields.Char(
        string="Emirates ID",
        compute="_compute_emarates_id",
        store=True,
        readonly=False,
        copy=False,
    )


    @api.depends("partner_id.emarates_id")
    def _compute_emarates_id(self):
        for lead in self:
            if lead.partner_id.emarates_id:
                lead.emarates_id = lead.partner_id.emarates_id


    def _sync_emarates_id_to_partner(self):
        for lead in self:
            partner = lead.partner_id
            if partner and lead.emarates_id and partner.emarates_id != lead.emarates_id:
                partner.sudo().emarates_id = lead.emarates_id

    @api.model_create_multi
    def create(self, vals_list):
        leads = super().create(vals_list)
        leads._sync_emarates_id_to_partner()
        return leads

    def write(self, vals):
        res = super().write(vals)
        if "emarates_id" in vals or "partner_id" in vals:
            self._sync_emarates_id_to_partner()
        return res
    
    def _prepare_customer_values(self, partner_name, *args, **kwargs):
        values = super()._prepare_customer_values(partner_name, *args, **kwargs)
        is_company = kwargs.get("is_company", args[0] if args else False)
        # The Emirates ID belongs to the person, not to the company record
        if self.emarates_id and not is_company:
            values["emarates_id"] = self.emarates_id
        return values
