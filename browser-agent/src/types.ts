export type BrowserMode = "headed" | "headless";

export interface InteractiveElement {
  role: string;
  text: string;
  selector: string;
  value?: string;
  bbox: [number, number, number, number];
}

export interface MediaState {
  playing: boolean;
  title: string;
  platform: string;
}

export interface PageState {
  url: string;
  title: string;
  interactive: InteractiveElement[];
  media: MediaState;
  scrollY: number;
  tabCount: number;
}

export interface ActionRequest {
  action: string;
  selector?: string;
  text?: string;
  url?: string;
  key?: string;
  direction?: "up" | "down";
  amount?: number;
  seconds?: number;
  engine?: string;
  query?: string;
  mediaAction?: string;
}

export interface ActionResult {
  success: boolean;
  message: string;
  state?: PageState;
}