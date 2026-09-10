//go:build integration

package xiaohongshu

import (
	"context"
	"testing"

	"github.com/go-rod/rod"
	"github.com/go-rod/rod/lib/launcher"
	"github.com/stretchr/testify/require"
)

func TestHumanSearchTypingAndFilterClicks(t *testing.T) {
	controlURL := launcher.New().Headless(true).MustLaunch()
	browser := rod.New().ControlURL(controlURL).MustConnect()
	defer browser.MustClose()

	page := browser.MustPage("about:blank")
	page.MustSetDocumentContent(`
		<!doctype html>
		<style>
			body { margin: 0; padding: 40px; font-family: sans-serif; }
			.input-box { display: flex; width: 420px; }
			.search-input { width: 380px; height: 42px; }
			.input-button { width: 42px; height: 42px; background: #ddd; }
			.filter { margin-top: 24px; width: 100px; height: 42px; background: #eee; }
			.filter-panel { display: none; margin-top: 12px; width: 520px; }
			.filters { display: flex; gap: 8px; margin: 10px 0; }
			.tags { min-width: 72px; height: 36px; background: #eee; }
		</style>
		<div class="input-box">
			<input class="search-input" placeholder="搜索小红书">
			<div class="input-button" onclick="document.body.dataset.query=document.querySelector('.search-input').value">搜索</div>
		</div>
		<div class="filter" onclick="document.querySelector('.filter-panel').style.display='block'">筛选</div>
		<div class="filter-panel">
			<div class="filters">
				<div class="tags">综合</div>
				<div class="tags" onclick="this.dataset.selected='true'">最新</div>
			</div>
			<div class="filters">
				<div class="tags">不限</div>
				<div class="tags">视频</div>
				<div class="tags" onclick="this.dataset.selected='true'">图文</div>
			</div>
		</div>
		<script>window.__INITIAL_STATE__ = { search: { feeds: { value: [] } } };</script>
	`)

	require.NoError(t, performHumanSearch(context.Background(), page, "凉拌鸡腿肉"))
	query := page.MustElement("body").MustAttribute("data-query")
	require.NotNil(t, query)
	require.Equal(t, "凉拌鸡腿肉", *query)

	require.NoError(t, applyHumanFilters(context.Background(), page, []internalFilterOption{
		{FiltersIndex: 1, TagsIndex: 2, Text: "最新"},
		{FiltersIndex: 2, TagsIndex: 3, Text: "图文"},
	}))

	latest := page.MustElement(`div.filters:nth-child(1) div.tags:nth-child(2)`).MustAttribute("data-selected")
	require.NotNil(t, latest)
	require.Equal(t, "true", *latest)

	imageText := page.MustElement(`div.filters:nth-child(2) div.tags:nth-child(3)`).MustAttribute("data-selected")
	require.NotNil(t, imageText)
	require.Equal(t, "true", *imageText)

	page.MustSetDocumentContent(`
		<!doctype html>
		<style>.search-input { width: 380px; height: 42px; }</style>
		<input class="search-input" placeholder="登录探索更多内容">
	`)
	err := performHumanSearch(context.Background(), page, "周末徒步")
	require.ErrorContains(t, err, "当前未登录")
}
