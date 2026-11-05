/** @odoo-module **/
import { registry } from "@web/core/registry";
import { Component, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";

/**
 * "Add Component" button for sale.order.line rows.
 *
 * 1. Saves the order first ONLY if it is new or dirty.
 * 2. Calls the existing `action_add_dynamic_component` method.
 * 3. Reloads the form so the new component line shows under its parent.
 */
export class AddComponentButton extends Component {
    static template = "optical_prescription.AddComponentButton";
    static props = { ...standardWidgetProps };

    setup() {
        this.orm = useService("orm");
        this.state = useState({ loading: false });
    }

    /**
     * Visibility rules.
     * A field that is NOT loaded in the view is `undefined`; such a value must
     * never hide the button by accident, so every test below only hides the
     * button on an explicit "no" (false / service / a real parent line ...).
     */
    get isVisible() {
        const record = this.props.record;
        const data = record.data;
        const order = record.model.root.data;

        // product must be chosen
        if (!data.product_id) {
            return false;
        }

        // many2one value: {id, display_name} (v17+) or [id, name]
        const productName = data.product_id.display_name ?? data.product_id[1] ?? "";

        // lab order flag: on the line, else on the order. Only an explicit
        // false hides the button (undefined = field not loaded = allowed)
        const isLabOrder = data.is_lab_order ?? order.is_lab_order;
        if (isLabOrder === false) {
            return false;
        }

        // a component line cannot have components, nor can section/note lines
        if (data.parent_line_id || data.display_type) {
            return false;
        }
        // services, down payments, discount / reward lines
        if (data.product_type === "service") {
            return false;
        }
        if (productName === "Discount") {
            return false;
        }
        return true;
    }

    async onClick() {
        if (this.state.loading) {
            return;
        }
        this.state.loading = true;
        try {
            const lineRecord = this.props.record;
            const formRecord = lineRecord.model.root;

            if (formRecord.isNew || formRecord.dirty) {
                const saved = await formRecord.save({ stayInEdition: true });
                if (!saved) {
                    return; // validation error already shown by the framework
                }
            }
            if (!lineRecord.resId) {
                console.warn("add_component_button: line has no resId yet, aborting this click.");
                return;
            }

            await this.orm.call("sale.order.line", "action_add_dynamic_component", [
                lineRecord.resId,
            ]);
            await formRecord.load();
        } finally {
            this.state.loading = false;
        }
    }
}

// Makes the list load these fields even if they are not columns of the view.
export const addComponentButton = {
    component: AddComponentButton,
    fieldDependencies: [
        { name: "product_id", type: "many2one" },
        { name: "is_lab_order", type: "boolean" },
        { name: "product_type", type: "selection" },
        { name: "parent_line_id", type: "many2one" },
        { name: "display_type", type: "selection" },
    ],
};

registry.category("view_widgets").add("add_component_button", addComponentButton);