from odoo import api, fields, models


class StockMove(models.Model):
    _inherit = "stock.move"

    intercompany_transfer_id = fields.Many2one(
        "intercompany.stock.transfer",
        string="Inter-Company Transfer",
        copy=False,
        index=True,
        ondelete="set null",
    )
    intercompany_source_transfer_id = fields.Many2one(
        "intercompany.stock.transfer",
        string="Inter-Company Source Transfer",
        copy=False,
        index=True,
        ondelete="set null",
    )
    intercompany_destination_transfer_id = fields.Many2one(
        "intercompany.stock.transfer",
        string="Inter-Company Destination Transfer",
        copy=False,
        index=True,
        ondelete="set null",
    )
    is_intercompany_transfer = fields.Boolean(
        string="Inter-Company Transfer",
        compute="_compute_is_intercompany_transfer",
        store=True,
        index=True,
    )

    @api.depends(
        "intercompany_transfer_id",
        "intercompany_source_transfer_id",
        "intercompany_destination_transfer_id",
    )
    def _compute_is_intercompany_transfer(self):
        for move in self:
            move.is_intercompany_transfer = bool(
                move.intercompany_transfer_id
                or move.intercompany_source_transfer_id
                or move.intercompany_destination_transfer_id
            )

    def _is_in(self):
        if self.is_intercompany_transfer:
            return False
        return super()._is_in()

    def _is_out(self):
        if self.is_intercompany_transfer:
            return False
        return super()._is_out()

    def _is_dropshipped(self):
        if self.is_intercompany_transfer:
            return False
        return super()._is_dropshipped()

    def _is_dropshipped_returned(self):
        if self.is_intercompany_transfer:
            return False
        return super()._is_dropshipped_returned()

    # def _get_valued_qty(self, lot=None):
    #     if self.is_intercompany_transfer:
    #         return 0
    #     return super()._get_valued_qty(lot=lot)
    #
    # def _get_value(self, *args, **kwargs):
    #     if self.is_intercompany_transfer:
    #         return 0
    #     return super()._get_value(*args, **kwargs)

    def _get_valued_qty(self, lot=None):
        """
        Intercompany valuation:
        - Source move: no valuation here because stock leaves Company A.
        - Destination move: valuation is created when Company B receives stock.
        """
        if self.is_intercompany_transfer:
            if self.intercompany_destination_transfer_id:
                return super()._get_valued_qty(lot=lot)

            return 0

        return super()._get_valued_qty(lot=lot)

    def _get_value(self, *args, **kwargs):
        """
        Allow valuation only on destination intercompany moves.
        """
        if self.is_intercompany_transfer:
            if self.intercompany_destination_transfer_id:
                return super()._get_value(*args, **kwargs)

            return 0

        return super()._get_value(*args, **kwargs)

    # def _create_account_move(self):
    #     # Odoo 19 stock_account calls this from stock.move._action_done().
    #     # Returning an empty recordset for our marked moves is an additional
    #     # isolation guard. Normal moves use the native implementation.
    #     non_intercompany = self.filtered(lambda m: not m.is_intercompany_transfer)
    #     if not non_intercompany:
    #         return self.env["account.move"]
    #     return super(StockMove, non_intercompany)._create_account_move()

    def _create_account_move(self):
        """
        Intercompany:
        - Source moves: no valuation journal entry.
        - Destination moves: allow normal stock valuation accounting.
        """
        non_intercompany = self.filtered(
            lambda m: not m.is_intercompany_transfer
        )

        destination_intercompany = self.filtered(
            lambda m:
            m.is_intercompany_transfer
            and m.intercompany_destination_transfer_id
        )

        moves_to_value = non_intercompany | destination_intercompany

        if not moves_to_value:
            return self.env["account.move"]

        return super(
            StockMove,
            moves_to_value
        )._create_account_move()