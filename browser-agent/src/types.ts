export type BrowserMode = "headed" | "headless";
export type ObservationStatus = "success" | "partial" | "needs_user_input";

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

export interface BrowserObservation {
  status: ObservationStatus;
  current_url: string;
  last_actions: string[];
  extracted_data: Record<string, unknown>;
  screenshot_path?: string;
  screenshot_base64?: string;
  voice_message: string;
  next_options: string[];
  state: PageState;
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
  allowNewTab?: boolean;
  volume?: number;
}

export interface ActionResult {
  success: boolean;
  message: string;
  state?: PageState;
  observation?: BrowserObservation;
}