"use client";

import { useEffect, useRef } from "react";

export function useChatScroll(dependency: any[]) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, dependency);

  return scrollRef;
}
