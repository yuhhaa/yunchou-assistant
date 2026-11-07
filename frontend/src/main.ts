import { createApp } from "vue";
import { createPinia } from "pinia";
import { createRouter, createWebHashHistory } from "vue-router";
import App from "./App.vue";
import Workspace from "./views/Workspace.vue";
import Login from "./views/Login.vue";
import { useOps } from "./stores/ops";
import "@fontsource/manrope/latin-400.css";
import "@fontsource/manrope/latin-500.css";
import "@fontsource/manrope/latin-600.css";
import "@fontsource/manrope/latin-700.css";
import "@fontsource/dm-mono/latin-400.css";
import "./styles.css";
import "./components/maps/maps.css";
import "./workspace-upgrade.css";
import "./driver-responsive.css";
import "./components/maps/amap.css";
import "./design-upgrade.css";
import "./product-refinement.css";
const router = createRouter({
  history: createWebHashHistory(),
  scrollBehavior: () => ({ top: 0 }),
  routes: [
    { path: "/", redirect: "/login" },
    { path: "/login", component: Login },
    ...["overview", "incidents", "plans", "driver"].map((path) => ({
      path: "/" + path,
      component: Workspace,
    })),
    { path: "/:pathMatch(.*)*", redirect: "/login" },
  ],
});
const app = createApp(App).use(createPinia());
router.beforeEach((to, from) => {
  const store = useOps();
  if (to.path !== from.path) store.cancelPolling();
  if (to.path !== "/login" && !store.user) return "/login";
  if (store.user && to.path !== "/login") {
    if (store.user.role === "DRIVER" && to.path !== "/driver") return "/driver";
    if (store.user.role === "DISPATCHER" && to.path === "/driver")
      return "/overview";
  }
});
app.use(router).mount("#app");
