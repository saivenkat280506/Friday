import puppeteerCore from "puppeteer";
import { addExtra } from "puppeteer-extra";
import StealthPlugin from "puppeteer-extra-plugin-stealth";
import type { Browser, Page, PuppeteerNode } from "puppeteer";

const puppeteer = addExtra(puppeteerCore as unknown as PuppeteerNode);
puppeteer.use(StealthPlugin());

export { puppeteer };
export type { Browser, Page };