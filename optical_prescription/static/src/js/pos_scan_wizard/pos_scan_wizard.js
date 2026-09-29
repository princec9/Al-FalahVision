/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

/**
 * POS Lot / Serial Scan Wizard.
 *
 * Front-end counterpart of the "Scan" action already used on the
 * Delivery form (stock.picking -> delivery.scan.wizard, see
 * optical_prescription/models/sale_order.py). That wizard, its views
 * and its model are NOT touched by this file.
 *
 * All the actual business validation (does the lot exist, is it
 * expired, is it the correct FEFO lot, ...) is delegated to the
 * EXISTING `pos.config.validate_pos_lot_scan` method already defined
 * in optical_prescription/models/hr_employee.py. This dialog only
 * drives the UI and loops over the lines that need to be scanned -
 * it does not re-implement any business rule.
 *
 * Usage: this.dialog.add(PosScanWizard, { configId, rows, onAllScanned })
 *   - configId: id of the current pos.config
 *   - rows: [{ productId, productName, lotName }] one row per lot/serial
 *           number that must be scanned (built by the control-buttons
 *           patch from the current order's lines).
 *   - onAllScanned: optional callback fired once every row is scanned
 *           and the user clicks "Confirm Scan".
 */
export class PosScanWizard extends Component {
    static template = "optical_prescription.PosScanWizard";
    static components = { Dialog };
    static props = {
        close: Function,
        configId: Number,
        rows: Array,
        onAllScanned: { type: Function, optional: true },
    };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({
            barcode: "",
            busy: false,
            // Local working copy - the props array itself is not mutated.
            rows: this.props.rows.map((r) => ({ ...r, scanned: false })),
        });
    }

    get scannedCount() {
        return this.state.rows.filter((r) => r.scanned).length;
    }

    get totalCount() {
        return this.state.rows.length;
    }

    get allScanned() {
        return this.totalCount > 0 && this.scannedCount === this.totalCount;
    }

    /**
     * Matching rule mirrors delivery.scan.wizard.action_scan: the lots
     * can be scanned in ANY order, matched purely by their lot/serial
     * number. Once matched, the scan is sent to the backend so the
     * existing expiry / FEFO rule can accept or reject it.
     */
    async onScan() {
        if (this.state.busy) {
            return;
        }
        const barcode = (this.state.barcode || "").trim();
        if (!barcode) {
            this.notification.add(_t("Please scan a barcode."), { type: "danger" });
            return;
        }

        const alreadyScanned = this.state.rows.find(
            (r) => r.scanned && r.lotName === barcode
        );
        if (alreadyScanned) {
            this.notification.add(
                _t("This lot/serial has already been scanned."),
                { type: "danger" }
            );
            this.state.barcode = "";
            return;
        }

        const row = this.state.rows.find(
            (r) => !r.scanned && r.lotName === barcode
        );
        if (!row) {
            this.notification.add(
                _t("This lot/serial is not required for this order."),
                { type: "danger" }
            );
            this.state.barcode = "";
            return;
        }

        if (!this.props.configId) {
            this.notification.add(
                _t("Could not determine the current POS configuration."),
                { type: "danger" }
            );
            return;
        }

        this.state.busy = true;
        try {
            // Same backend call/logic already used for FEFO/expiry
            // verification - nothing new is added on that side.
            //
            // `validate_pos_lot_scan` is an @api.model method: it takes
            // (config_id, values) directly, it is NOT bound to a
            // recordset, so the args list must be
            // [configId, values] - NOT [[configId], values].
            const result = await this.orm.call(
                "pos.config",
                "validate_pos_lot_scan",
                [this.props.configId, {
                    product_id: row.productId,
                    selected_lot: row.lotName,
                    scanned_lot: barcode,
                }]
            );

            if (!result || !result.valid) {
                this.notification.add(
                    (result && result.message) || _t("Invalid scan."),
                    { type: "danger" }
                );
                return;
            }

            row.scanned = true;
            if (result.message) {
                this.notification.add(result.message, { type: "success" });
            }
        } catch (error) {
            // Surface the real server/network error instead of a
            // generic message, to make this easier to diagnose.
            const serverMessage =
                error?.data?.message || error?.message?.data?.message || error?.message;
            this.notification.add(
                serverMessage || _t("Could not verify this scan. Please try again."),
                { type: "danger" }
            );
            console.error("PosScanWizard.onScan failed:", error);
        } finally {
            this.state.busy = false;
            this.state.barcode = "";
        }
    }

    onConfirm() {
        if (!this.allScanned) {
            this.notification.add(
                _t("Not all lots/serials have been scanned yet."),
                { type: "danger" }
            );
            return;
        }
        if (this.props.onAllScanned) {
            this.props.onAllScanned();
        }
        this.props.close();
    }

    onCancel() {
        this.props.close();
    }
}