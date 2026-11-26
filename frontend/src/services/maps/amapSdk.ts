// Minimal SDK boundary. Loader's untyped result is narrowed here, outside business types.
export type LngLat = [number, number];
export type AmapClick = { lnglat?: { getLng(): number; getLat(): number } };
export interface AmapOverlay {
  setMap(map: AmapInstance | null): void;
  on(name: string, callback: () => void): void;
}
export interface AmapInstance {
  on(name: string, callback: (event: AmapClick) => void): void;
  off(name: string, callback: (event: AmapClick) => void): void;
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
  setPitch?(pitch: number, immediately?: boolean, duration?: number): void;
  setRotation?(
    rotation: number,
    immediately?: boolean,
    duration?: number,
  ): void;
  getZoom?(): number;
  setMapStyle?(style: string): void;
  add?(layer: unknown): void;
  getCenter?(): unknown;
  setZoom?(zoom: number, immediately?: boolean, duration?: number): void;
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
  Buildings?: new (options?: Record<string, unknown>) => AmapLayer;
}
export interface AmapPolylineLike extends AmapOverlay {
  setPath?(path: LngLat[]): void;
  setOptions?(options: Record<string, unknown>): void;
  moveAlong?(path: LngLat[], options: Record<string, unknown>): void;
  stopMove?(): void;
}

export interface AmapLayer {
  setMap?(map: AmapInstance | null): void;
  show?(): void;
  hide?(): void;
}
