/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { ControlButtons } from "@point_of_sale/app/screens/product_screen/control_buttons/control_buttons";
import { PosScanWizard } from "./pos_scan_wizard";

/**
 * Adds a "Scan" button to the POS control panel.
 *
 * This mirrors the existing "Scan" button already on the Delivery form
 * (optical_prescription/views/sale.xml -> action_quality_checks /
 * delivery.scan.wizard) but for a POS order: when a Quotation/Order is
 * loaded into POS (or lots are entered directly in POS) and it contains
 * lines for lot/serial tracked products with a lot number entered, the
 * cashier can tap "Scan" to verify each lot the same way the warehouse
 * verifies a delivery.
 *
 * IMPORTANT: this file only ADDS behaviour via patch() - nothing in the
 * existing ControlButtons component, template or POS flow is modified.
 * Payment/validation is left completely untouched; scanning here is
 * purely a verification step exposed through the new button.
 */
patch(ControlButtons.prototype, {
    /**
     * One row per lot/serial number that still needs to be verified:
     * every order line whose product is tracked by lot/serial and that
     * already has at least one lot/serial number entered (a line with
     * qty > 1 can carry several lots, hence one row per lot).
     *
     * NOTE: if your POS build exposes pack lot lines under a different
     * accessor than `pack_lot_ids` / `getPackLotLinesToEdit()`, adjust
     * the two lookups below - the rest of the file does not need to
     * change.
     */
    get scanLines() {
        const order = this.pos.getOrder();
        if (!order) {
            return [];
        }
        const rows = [];
        for (const line of order.getOrderlines()) {
            const product = line.product_id;
            if (!product || !["lot", "serial"].includes(product.tracking)) {
                continue;
            }
            const lots =
                (typeof line.getPackLotLinesToEdit === "function"
                    ? line.getPackLotLinesToEdit()
                    : line.pack_lot_ids) || [];
            for (const lot of lots) {
                const lotName = ((lot.lot_name ?? lot.text) || "").trim();
                if (!lotName) {
                    continue;
                }
                rows.push({
                    productId: product.id,
                    productName: product.display_name,
                    lotName,
                });
            }
        }
        return rows;
    },

    /** Same slot as the other top-row buttons: hidden inside the
     *  "More actions" popup, only shown on the main control row. */
    get showScanButton() {
        return !this.props.showRemainingButtons && this.scanLines.length > 0;
    },

    async onClickScan() {
        const rows = this.scanLines;
        if (!rows.length) {
            return;
        }
        const order = this.pos.getOrder();
        this.dialog.add(PosScanWizard, {
            configId: this.pos.config.id,
            rows,
            onAllScanned: () => {
                // Purely informational client-side flag for now - does
                // not gate payment or any other existing flow.
                order.lotScanCompleted = true;
            },
        });
    },
});
