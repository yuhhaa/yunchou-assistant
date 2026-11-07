// Isolated test harness; never mounted by product routes. All coordinates are synthetic.
import { createApp, h, ref, provide } from "vue";
import MapView from "../../src/components/maps/MapView.vue";
import { amapLoaderKey, loadAmap } from "../../src/services/maps/amapLoader";
import { mockRequest } from "../../src/mock/transport";
import type { Context } from "../../src/api/types";
import type { GeographicRouteData } from "../../src/components/maps/types";
import "../../src/styles.css";
import "../../src/components/maps/maps.css";
import "../../src/workspace-upgrade.css";
import "../../src/components/maps/amap.css";
const context = await mockRequest<Context>("/delivery-context?vehicle_id=V01");
const config = {
  key: "00000000000000000000000000000000",
  host: location.origin + "/_AMapService",
  configured: true,
};
const base = context.route_stops;
const geography: GeographicRouteData = {
  coordinateSystem: "GCJ02",
  dataKind: "DEMO",
  source: "SYNTHETIC_TEST_ONLY",
  version: "fixture-v1",
  observedAt: new Date().toISOString(),
  snapshotId: context.snapshot_id,
  snapshotVersion: context.snapshot_version,
  nodes: [
    ...base.map((s, i) => ({
      nodeId: s.node_id,
      coordinates: [120 + i * 0.01, 30 + i * 0.01] as [number, number],
    })),
    { nodeId: context.warehouse_node_id!, coordinates: [119.99, 29.99] },
  ],
  routes: [
    {
      vehicleId: "V01",
      planId: null,
      planVersion: null,
      routeRevision: 1,
      geometryKind: "ROAD",
      stopIds: base.map((s) => s.stop_id),
      coordinates: [
        [120, 30],
        [120.01, 30.01],
      ],
      role: "ORIGINAL",
    },
  ],
};
createApp({
  setup() {
    provide(amapLoaderKey, () => loadAmap(config));
    const selected = ref("O001"),
      count = ref(0),
      candidate = ref(false),
      visible = ref(true),
      refresh = ref(0);
    const target = base
      .map((s, i) => ({ ...s, sequence_no: base.length - i }))
      .reverse();
    return () =>
      h("main", { style: "max-width:1000px;margin:30px auto" }, [
        h(
          "p",
          { role: "note" },
          "TEST DOUBLE ONLY · 不代表真实地图瓦片或真实配送数据",
        ),
        h(
          "button",
          {
            class: "secondary",
            onClick: () => (candidate.value = !candidate.value),
          },
          "切换候选",
        ),
        h(
          "button",
          {
            class: "secondary",
            onClick: () => (visible.value = !visible.value),
          },
          "挂载或卸载地图",
        ),
        h(
          "button",
          { class: "secondary", onClick: () => refresh.value++ },
          "刷新相同路线",
        ),
        h(
          "button",
          {
            class: "secondary",
            onClick: async () => {
              await Promise.all([loadAmap(config), loadAmap(config)]);
              count.value++;
            },
          },
          "并发加载 SDK",
        ),
        ...base.map((s) =>
          h(
            "button",
            {
              class: "secondary",
              onClick: () => (selected.value = s.order_no),
            },
            "选择订单 " + s.order_no,
          ),
        ),
        visible.value
          ? h(MapView, {
              context,
              stops: candidate.value ? target : base,
              baseline: base,
              vehicleId: "V01",
              selectedOrder: selected.value,
              "onUpdate:selectedOrder": (v: string) => (selected.value = v),
              geography: {
                ...geography,
                observedAt: refresh.value
                  ? new Date().toISOString()
                  : geography.observedAt,
                routes: [
                  ...geography.routes,
                  {
                    vehicleId: "V01",
                    planId: "TEST_PLAN",
                    planVersion: 1,
                    routeRevision: null,
                    geometryKind: "ROAD",
                    stopIds: target.map((s) => s.stop_id),
                    coordinates: [
                      [120.03, 30.03],
                      [120.02, 30.02],
                    ],
                  },
                ],
              },
              candidate: candidate.value,
              planId: candidate.value ? "TEST_PLAN" : null,
              planVersion: candidate.value ? 1 : null,
              planStatus: "PENDING_APPROVAL",
              riskOrders: ["O002"],
              nextOrder: "O001",
              dataMode: "demo",
              title: "地图故障测试",
            })
          : null,
        h(
          "button",
          { class: "primary", onClick: () => count.value++ },
          "列表执行反馈 " + count.value,
        ),
      ]);
  },
}).mount("#fixture");
