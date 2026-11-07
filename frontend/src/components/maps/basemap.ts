import type { StyleSpecification } from "maplibre-gl";
export function basemapStyle(localDemo: boolean): StyleSpecification {
  return {
    version: 8,
    name: "Yunchou / Quiet City",
    sources: {
      "city-base": {
        type: "raster",
        tiles: [
          localDemo
            ? location.origin + "/maps/tiles/{z}/{x}/{y}.png"
            : "https://basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png",
        ],
        tileSize: 256,
        minzoom: localDemo ? 12 : 0,
        maxzoom: localDemo ? 16 : 19,
        attribution:
          '© <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a> © <a href="https://carto.com/attributions" target="_blank">CARTO</a>',
      },
    },
    layers: [
      {
        id: "paper",
        type: "background",
        paint: { "background-color": "#e9eddf" },
      },
      {
        id: "quiet-city",
        type: "raster",
        source: "city-base",
        paint: {
          "raster-saturation": -0.72,
          "raster-contrast": -0.08,
          "raster-brightness-min": 0.06,
          "raster-brightness-max": 0.98,
          "raster-fade-duration": 120,
        },
      },
    ],
  };
}
