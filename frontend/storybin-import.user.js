// ==UserScript==
// @name         Storybin Import For Xbanxia
// @namespace    https://storybin.onrender.com/
// @version      0.1.0
// @description  Fetch the current xbanxia novel in your real browser and send it to Storybin shared cache.
// @match        https://www.xbanxia.cc/books/*.html
// @grant        GM_addStyle
// @grant        GM_xmlhttpRequest
// @connect      storybin.onrender.com
// ==/UserScript==

(function () {
  "use strict";

  const DEFAULT_BACKEND_URL = "https://storybin.onrender.com";
  const intro = document.querySelector("div.book-intro");
  const chapterLinks = Array.from(document.querySelectorAll("div.book-list a[href]"));
  if (!intro || !chapterLinks.length) {
    return;
  }

  GM_addStyle(`
    #storybin-import-button {
      position: fixed;
      right: 20px;
      bottom: 20px;
      z-index: 99999;
      border: none;
      border-radius: 999px;
      padding: 12px 18px;
      color: #fff;
      background: linear-gradient(180deg, #9f4a1c, #7d3410);
      box-shadow: 0 16px 32px rgba(125, 52, 16, 0.28);
      font-size: 14px;
      cursor: pointer;
    }
    #storybin-import-button[disabled] {
      cursor: wait;
      opacity: 0.75;
    }
  `);

  const button = document.createElement("button");
  button.id = "storybin-import-button";
  button.type = "button";
  button.textContent = "导入到 Storybin";
  document.body.appendChild(button);

  button.addEventListener("click", async () => {
    button.disabled = true;
    const originalLabel = button.textContent;
    try {
      const novel = extractNovelMeta();
      const parts = [`《${novel.title}》`, `作者：${novel.author}`, ""];

      for (let index = 0; index < novel.chapterUrls.length; index += 1) {
        button.textContent = `抓取章节 ${index + 1}/${novel.chapterUrls.length}`;
        const chapter = await fetchChapter(novel.chapterUrls[index]);
        parts.push(`第${index + 1}章 ${chapter.title}`, "", chapter.body, "");
        await sleep(180);
      }

      button.textContent = "提交到 Storybin…";
      const payload = {
        source_filename: `${sanitizeFilename(novel.title)}.txt`,
        novel_url: novel.url,
        title: novel.title,
        author: novel.author,
        category: novel.category,
        latest_update: novel.latestUpdate,
        chapter_count: novel.chapterUrls.length,
        content_txt: `${parts.join("\n").trim()}\n`,
      };

      const result = await postJson(`${DEFAULT_BACKEND_URL}/contribute/cache`, payload);
      button.textContent = "导入成功";
      window.open(result.txt_download_url, "_blank", "noopener,noreferrer");
      alert(`已导入到 Storybin：${result.title}`);
    } catch (error) {
      console.error(error);
      alert(`导入失败：${error.message}`);
      button.textContent = "导入失败，点击重试";
    } finally {
      button.disabled = false;
      if (button.textContent === "导入成功") {
        setTimeout(() => {
          button.textContent = originalLabel;
        }, 2500);
      }
    }
  });

  function extractNovelMeta() {
    const title = text("div.book-describe h1") || document.title.replace(/\s+-\s+半夏小說$/, "");
    const author = extractMetaValue("作者") || "未知";
    const category = extractMetaValue("類型") || "用户导入";
    const latestUpdate = extractMetaValue("最近更新") || null;
    return {
      url: window.location.href,
      title,
      author,
      category,
      latestUpdate,
      chapterUrls: chapterLinks.map((link) => new URL(link.getAttribute("href"), window.location.href).toString()),
    };
  }

  function extractMetaValue(label) {
    const nodes = Array.from(document.querySelectorAll("div.book-describe p"));
    for (const node of nodes) {
      const raw = node.textContent.trim();
      if (!raw.startsWith(`${label}︰`) && !raw.startsWith(`${label}:`)) {
        continue;
      }
      const link = node.querySelector("a");
      return (link ? link.textContent : raw.split(/[︰:]/, 2)[1] || "").trim();
    }
    return "";
  }

  async function fetchChapter(url) {
    const response = await fetch(url, { credentials: "include" });
    if (!response.ok) {
      throw new Error(`章节抓取失败：HTTP ${response.status}`);
    }
    const html = await response.text();
    const doc = new DOMParser().parseFromString(html, "text/html");
    const title = textFrom(doc, "#nr_title") || "未命名章节";
    const bodyNode = doc.querySelector("#nr1");
    if (!bodyNode) {
      throw new Error(`章节正文缺失：${url}`);
    }
    const raw = bodyNode.textContent.replace(/\u00a0/g, " ");
    const lines = raw
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean);
    if (lines[0] === title) {
      lines.shift();
    }
    return {
      title,
      body: lines.join("\n"),
    };
  }

  function postJson(url, payload) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method: "POST",
        url,
        headers: { "Content-Type": "application/json" },
        data: JSON.stringify(payload),
        onload(response) {
          let parsed = {};
          try {
            parsed = JSON.parse(response.responseText || "{}");
          } catch (error) {
            parsed = {};
          }
          if (response.status < 200 || response.status >= 300) {
            reject(new Error(parsed.detail || `HTTP ${response.status}`));
            return;
          }
          resolve(parsed);
        },
        onerror() {
          reject(new Error("Storybin 请求失败"));
        },
      });
    });
  }

  function text(selector) {
    const node = document.querySelector(selector);
    return node ? node.textContent.trim() : "";
  }

  function textFrom(doc, selector) {
    const node = doc.querySelector(selector);
    return node ? node.textContent.trim() : "";
  }

  function sanitizeFilename(value) {
    return value.replace(/[\\\\/:*?\"<>|]/g, "_").trim() || "storybin-import";
  }

  function sleep(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
  }
})();
