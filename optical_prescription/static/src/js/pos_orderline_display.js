/** @odoo-module **/

import { Orderline } from "@point_of_sale/app/components/orderline/orderline";
import { formatCurrency } from "@web/core/currency";
import { patch } from "@web/core/utils/patch";

/**
 * Show the discount as an amount next to the standard percentage.
 * Read-only mirror -- it never affects the price actually charged.
 */
patch(Orderline.prototype, {
    get lineScreenValues() {
        const vals = super.lineScreenValues;
        const line = this.line;

        // super returns {} when the line isn't linked to an order yet
        if (!line?.order_id || !line.discount) {
            return vals;
        }

        const perUnit = line.discountAmountPerUnit;
        if (!perUnit) {
            return vals;
        }

        vals.discountAmount = this.props.basic_receipt
            ? false
            : formatCurrency(line.discountAmountTotal, line.currency.id);
        vals.discountAmountUnit = formatCurrency(perUnit, line.currency.id);
        return vals;
    },
});
