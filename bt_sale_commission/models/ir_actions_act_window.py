# -*- coding: utf-8 -*-
"""
Re-point Odoo's native Commission AND Achievements report actions (user-keyed)
to the partner-based reports, so every menu/button that opened it shows sales people
(partners). Originals are stored so uninstall restores them.
Runs on every install/upgrade (see data/commission_report_data.xml).
"""
import json
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)
PARAM = 'bt_sale_commission.replaced_actions'
from .sale_commission_plan import REPOINT


class IrActionsActWindow(models.Model):
    _inherit = 'ir.actions.act_window'

    @api.model
    def _bt_repoint_native_commission_report(self):
        param = self.env['ir.config_parameter'].sudo()
        # ON by default. Set system parameter
        # 'bt_sale_commission.repoint_native_report' to 0 and upgrade the
        # module to leave the native (user-based) reports as stock Odoo.
        if param.get_param('bt_sale_commission.repoint_native_report') in ('0', 'False', 'false'):
            self._bt_restore_native_commission_report()
            return
        saved = json.loads(param.get_param(PARAM) or '{}')
        for native_model, xmlid in REPOINT.items():
            ours = self.env.ref(xmlid, raise_if_not_found=False)
            if not ours:
                continue
            natives = self.sudo().search([
                ('res_model', '=', native_model), ('id', '!=', ours.id),
            ])
            if not natives:
                _logger.warning(
                    "bt_sale_commission: no window action found for %s; it "
                    "was not re-pointed.", native_model)
            our_views = [(v.view_mode, v.view_id.id) for v in ours.view_ids.sorted('sequence')]
            for action in natives:
                saved.setdefault(str(action.id), {
                    'res_model': action.res_model,
                    'domain': action.domain,
                    'context': action.context,
                    'view_mode': action.view_mode,
                    'view_id': action.view_id.id or False,
                    'search_view_id': action.search_view_id.id or False,
                    'views': [(v.view_mode, v.view_id.id) for v in action.view_ids.sorted('sequence')],
                })
                action.view_ids.unlink()
                action.write({
                    'res_model': ours.res_model,
                    'domain': ours.domain,
                    'context': ours.context,
                    'view_mode': ours.view_mode,
                    'view_id': False,
                    'search_view_id': ours.search_view_id.id,
                })
                for seq, (mode, view_id) in enumerate(our_views, start=1):
                    self.env['ir.actions.act_window.view'].sudo().create({
                        'act_window_id': action.id, 'sequence': seq,
                        'view_mode': mode, 'view_id': view_id,
                    })
                _logger.info("bt_sale_commission: action %r (id=%s, was %s) now opens "
                             "the partner-based report.", action.name, action.id, native_model)
        param.set_param(PARAM, json.dumps(saved))

    @api.model
    def _bt_restore_native_commission_report(self):
        param = self.env['ir.config_parameter'].sudo()
        saved = json.loads(param.get_param(PARAM) or '{}')
        for action_id, vals in saved.items():
            action = self.sudo().browse(int(action_id)).exists()
            if not action:
                continue
            action.view_ids.unlink()
            action.write({k: vals[k] for k in (
                'res_model', 'domain', 'context', 'view_mode', 'view_id', 'search_view_id')})
            for seq, (mode, view_id) in enumerate(vals.get('views', []), start=1):
                self.env['ir.actions.act_window.view'].sudo().create({
                    'act_window_id': action.id, 'sequence': seq,
                    'view_mode': mode, 'view_id': view_id,
                })
        param.set_param(PARAM, '{}')
