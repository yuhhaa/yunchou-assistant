// Minimal SDK boundary. Loader's untyped result is narrowed here, outside business types.
export type LngLat = [number, number];
export interface AmapOverlay {
  setMap(map: AmapInstance | null): void;
  on(name: string, callback: () => void): void;
}
export interface AmapInstance {
  on(name: string, callback: () => void): void;
  off(name: string, callback: () => void): void;
  destroy(): void;
  addControl(control: unknown): void;
  setFitView(
    overlays?: AmapOverlay[],
    immediately?: boolean,
    padding?: number[],
    maxZoom?: number,
  ): void;
  setZoomAndCenter(zoom: number, center: LngLat, immediately?: boolean): void;
  zoomIn(): void;
  zoomOut(): void;
  resize?(): void;
}
export interface AmapInfoWindow {
  open(map: AmapInstance, position: LngLat): void;
  close(): void;
}
export interface AmapSDK {
  Map: new (
    host: HTMLElement,
    options: Record<string, unknown>,
  ) => AmapInstance;
  Marker: new (options: Record<string, unknown>) => AmapOverlay;
  Polyline: new (options: Record<string, unknown>) => AmapOverlay;
  InfoWindow: new (options: Record<string, unknown>) => AmapInfoWindow;
  Pixel: new (x: number, y: number) => unknown;
  Scale: new (options?: Record<string, unknown>) => unknown;
}
