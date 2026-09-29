import logging

_logger = logging.getLogger(__name__)
from odoo import models, _


class AccountTax(models.Model):
    _inherit = "account.tax"

    def _prepare_global_discount_lines(self, base_lines, *args, **kwargs):
        base_lines = [
            base_line
            for base_line in base_lines
            if not (
                    base_line.get("record")
                    and base_line["record"]._name == "sale.order.line"
                    and (
                            base_line["record"].price_subtotal == 0.0
                            or (
                                    base_line["record"].is_lab_order
                                    and not base_line["record"].parent_line_id
                            )
                    )
            )
        ]
        return super()._prepare_global_discount_lines(base_lines, *args, **kwargs)
