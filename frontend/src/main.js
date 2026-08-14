import { createApp } from "vue";
import FrappeBadge from "frappe-ui/src/components/Badge/Badge.vue";
import App from "./App.vue";
import "./style.css";

const app = createApp(App);
app.component("FrappeBadge", FrappeBadge);
app.mount("#app");
