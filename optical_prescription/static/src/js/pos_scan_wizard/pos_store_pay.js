/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { PosScanWizard } from "./pos_scan_wizard";

patch(PosStore.prototype, {
    async pay() {
        if (this._lotScanBypass) {
            this._lotScanBypass = false;
            return super.pay(...arguments);
        }

        const order = this.getOrder();

        if (!order) {
            return super.pay(...arguments);
        }

        const scanLines = [];

        for (const line of order.getOrderlines()) {
            const product = line.product_id;

            if (!product || !["lot", "serial"].includes(product.tracking)) {
                continue;
            }

            const lots =
                (
                    typeof line.getPackLotLinesToEdit === "function"
                        ? line.getPackLotLinesToEdit()
                        : line.pack_lot_ids
                ) || [];

            for (const lot of lots) {
                const lotName = ((lot.lot_name ?? lot.text) || "").trim();

                if (!lotName) {
                    continue;
                }

                scanLines.push({
                    productId: product.id,
                    productName: product.display_name,
                    lotName,
                });
            }
        }

        if (!scanLines.length) {
            return super.pay(...arguments);
        }

        this.dialog.add(PosScanWizard, {
            configId: this.config.id,
            rows: scanLines,

            onAllScanned: async () => {
                this._lotScanBypass = true;

                try {
                    await this.pay();
                } catch (error) {
                    this._lotScanBypass = false;
                    throw error;
                }
            },
        });

        return;
    },
});