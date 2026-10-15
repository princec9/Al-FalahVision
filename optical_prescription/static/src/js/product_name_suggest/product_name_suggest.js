/** @odoo-module **/
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { useDebounced } from "@web/core/utils/timing";
import { useState } from "@odoo/owl";
import { CharField, charField } from "@web/views/fields/char/char_field";

export class ProductNameSuggestField extends CharField {
    static template = "optical_prescription.ProductNameSuggestField";

    setup() {
        super.setup();
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ suggestions: [], open: false });
        this.debouncedSearch = useDebounced(this.searchProducts.bind(this), 300);
    }

    onNameInput(ev) {
        const term = (ev.target.value || "").trim();
        if (term.length < 2) {
            this.state.suggestions = [];
            this.state.open = false;
            return;
        }
        this.debouncedSearch(term);
    }

    async searchProducts(term) {
        const domain = [["name", "ilike", term]];
        const resId = this.props.record.resId;
        if (resId) {
            domain.push(["id", "!=", resId]);
        }
        this.state.suggestions = await this.orm.searchRead(
            "product.template",
            domain,
            ["name", "default_code"],
            { limit: 8 }
        );
        this.state.open = this.state.suggestions.length > 0;
    }

    onBlur() {
        // delay so a click on a suggestion still registers
        setTimeout(() => (this.state.open = false), 200);
    }

    openProduct(id) {
        this.state.open = false;
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "product.template",
            res_id: id,
            views: [[false, "form"]],
            target: "new",
        });
    }
}

registry.category("fields").add("product_name_suggest", {
    ...charField,
    component: ProductNameSuggestField,
});