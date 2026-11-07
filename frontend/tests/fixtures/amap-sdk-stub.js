// TEST DOUBLE ONLY. No real map tiles or service results are produced.
window.__amapTest = {
  created: 0,
  destroyed: 0,
  fit: 0,
  focus: 0,
  zoom: 0,
  resized: 0,
  lines: [],
};
class MapStub {
  constructor(host) {
    this.host = host;
    this.events = {};
    window.__amapTest.created++;
    host.dataset.testDouble = "true";
  }
  on(name, cb) {
    this.events[name] = cb;
    if (name === "complete") setTimeout(cb, 10);
  }
  off(name) {
    delete this.events[name];
  }
  destroy() {
    window.__amapTest.destroyed++;
    this.host.replaceChildren();
  }
  addControl() {}
  setFitView() {
    window.__amapTest.fit++;
  }
  setZoomAndCenter() {
    window.__amapTest.focus++;
  }
  zoomIn() {
    window.__amapTest.zoom++;
  }
  zoomOut() {
    window.__amapTest.zoom--;
  }
  resize() {
    window.__amapTest.resized++;
  }
}
class OverlayStub {
  constructor(options) {
    this.options = options;
    this.events = {};
    if (options.content) {
      this.node = options.content;
      this.node.addEventListener("click", () => this.events.click?.());
    } else window.__amapTest.lines.push(options);
    this.setMap(options.map);
  }
  on(name, cb) {
    this.events[name] = cb;
  }
  setMap(map) {
    if (!this.node) return;
    if (map) map.host.append(this.node);
    else this.node.remove();
  }
}
window.AMap = {
  Map: MapStub,
  Marker: OverlayStub,
  Polyline: OverlayStub,
  Pixel: class {},
  Scale: class {},
  InfoWindow: class {
    constructor(options) {
      this.node = options.content;
    }
    open(map) {
      map.host.append(this.node);
    }
    close() {
      this.node.remove();
    }
  },
};
window.___onAPILoaded();
