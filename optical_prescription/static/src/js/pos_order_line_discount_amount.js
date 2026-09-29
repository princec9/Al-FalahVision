/** @odoo-module **/

import { PosOrderline } from "@point_of_sale/app/models/pos_order_line";
import { patch } from "@web/core/utils/patch";

/**
 * DISCOUNT AMOUNT -- DISPLAY ONLY (POS)
 *
 * The discount amount is derived from the standard POS `discount` (%) and
 * is shown purely for reference. Pricing, taxes and totals in the POS keep
 * following Odoo's standard flow using the percentage alone.
 *
 * NOTE: `prepareBaseLineForTaxesComputationExtraValues` is intentionally
 * NOT patched any more -- it previously subtracted the amount a second
 * time on top of the percentage.
 */
patch(PosOrderline.prototype, {
    /** Discount amount for one unit, derived from the percentage. */
    get discountAmountPerUnit() {
        const priceUnit = this.price_unit || 0;
        const discount = this.discount || 0;
        return (priceUnit * discount) / 100.0;
    },

    /** Discount amount for the whole line, derived from the percentage. */
    get discountAmountTotal() {
        return this.discountAmountPerUnit * Math.abs(this.qty || 0);
    },
});
