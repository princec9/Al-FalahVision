# -*- coding: utf-8 -*-
"""
Sale Order duplication fix - component hierarchy preservation.

PURPOSE
-------
When a Sale Order containing "Add Component" lines is duplicated, the
copied order used to lose the parent/child relationship, so component
lines appeared as ordinary product lines.

This module is strictly ADDITIVE:
  * no existing method is modified,
  * no existing field definition is touched,
  * only ``copy_data`` / ``copy`` of ``sale.order`` (and ``copy_data``
    of ``sale.order.line``, guarded by a context key that is only ever
    set while an order is being duplicated) are extended.

HOW IT WORKS
------------
1. While the order is being copied, each copied line drops the raw
   ``parent_line_id`` value, because that value still points to a line
   of the ORIGINAL order (a cross-order reference, which is exactly
   what makes the component look like a plain product line).
2. After the new order exists, the parent links are rebuilt from the
   ORIGINAL order structure, using the position of each line, and are
   written on the NEW lines so that every component points to its own
   new parent inside the new order.

Nothing else is written, so pricing, quantities, stock, invoicing,
confirmation and UI behaviour stay exactly as before.
"""

import logging

from odoo import models

_logger = logging.getLogger(__name__)

# Context key set only during a sale.order duplication.
COPY_CTX_KEY = "optical_prescription_copying_order"


class SaleOrder(models.Model):
    _inherit = "sale.order"

    def copy_data(self, default=None):
        """Flag the duplication so component lines can be handled."""
        return super(
            SaleOrder,
            self.with_context(**{COPY_CTX_KEY: True}),
        ).copy_data(default=default)

    def copy(self, default=None):
        """Duplicate the order, then rebuild the component hierarchy."""
        new_orders = super().copy(default=default)

        # ``copy`` returns the new records in the same order as ``self``.
        for old_order, new_order in zip(self, new_orders):
            old_order._optical_restore_component_links(new_order)

        return new_orders

    def _optical_restore_component_links(self, new_order):
        """Re-create parent/child links on the duplicated order.

        The mapping is positional: ``sale.order.line`` records are read
        in the same (sequence, id) order on both orders, and the copied
        lines are created in exactly that order, so index ``i`` of the
        source order corresponds to index ``i`` of the new order.
        """
        self.ensure_one()

        old_lines = self.order_line
        new_lines = new_order.order_line

        # Nothing to rebuild, or the two orders are not comparable
        # (e.g. lines added/removed by another override during copy):
        # in that case leave the new order untouched.
        if not old_lines or len(old_lines) != len(new_lines):
            if old_lines and any(line.parent_line_id for line in old_lines):
                _logger.warning(
                    "Sale Order %s: component hierarchy could not be "
                    "restored on the duplicated order %s (%s source lines "
                    "vs %s copied lines).",
                    self.display_name,
                    new_order.display_name,
                    len(old_lines),
                    len(new_lines),
                )
            return

        # old line id -> corresponding new line record
        line_map = {
            old_line.id: new_line
            for old_line, new_line in zip(old_lines, new_lines)
        }

        for old_line, new_line in zip(old_lines, new_lines):
            old_parent = old_line.parent_line_id
            new_parent = line_map.get(old_parent.id) if old_parent else False

            expected_id = new_parent.id if new_parent else False
            if new_line.parent_line_id.id != expected_id:
                # Written with the same context guard removed, so no
                # other override can mistake this for a fresh copy.
                new_line.with_context(
                    **{COPY_CTX_KEY: False}
                ).parent_line_id = expected_id


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    def copy_data(self, default=None):
        """Drop the stale parent reference while copying a whole order.

        The link is restored right after the new order is created, in
        :meth:`SaleOrder._optical_restore_component_links`. When a line
        is duplicated on its own (outside an order copy), behaviour is
        unchanged.
        """
        vals_list = super().copy_data(default=default)

        if self.env.context.get(COPY_CTX_KEY):
            for vals in vals_list:
                vals.pop("parent_line_id", None)

        return vals_list
