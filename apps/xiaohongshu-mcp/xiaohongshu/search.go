package xiaohongshu

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/go-rod/rod"
	"github.com/sirupsen/logrus"
	"github.com/xpzouying/xiaohongshu-mcp/errors"
)

type SearchResult struct {
	Search struct {
		Feeds FeedsValue `json:"feeds"`
	} `json:"search"`
}

// FilterOption 筛选选项结构体
type FilterOption struct {
	SortBy      string `json:"sort_by,omitempty" jsonschema:"排序依据: 综合|最新|最多点赞|最多评论|最多收藏,默认为'综合'"`
	NoteType    string `json:"note_type,omitempty" jsonschema:"笔记类型: 不限|视频|图文,默认为'不限'"`
	PublishTime string `json:"publish_time,omitempty" jsonschema:"发布时间: 不限|一天内|一周内|半年内,默认为'不限'"`
	SearchScope string `json:"search_scope,omitempty" jsonschema:"搜索范围: 不限|已看过|未看过|已关注,默认为'不限'"`
	Location    string `json:"location,omitempty" jsonschema:"位置距离: 不限|同城|附近,默认为'不限'"`
}

// internalFilterOption 内部使用的筛选选项(基于索引)
type internalFilterOption struct {
	FiltersIndex int    // 筛选组索引
	TagsIndex    int    // 标签索引
	Text         string // 标签文本描述
}

// 预定义的筛选选项映射表（内部使用）
var filterOptionsMap = map[int][]internalFilterOption{
	1: { // 排序依据
		{FiltersIndex: 1, TagsIndex: 1, Text: "综合"},
		{FiltersIndex: 1, TagsIndex: 2, Text: "最新"},
		{FiltersIndex: 1, TagsIndex: 3, Text: "最多点赞"},
		{FiltersIndex: 1, TagsIndex: 4, Text: "最多评论"},
		{FiltersIndex: 1, TagsIndex: 5, Text: "最多收藏"},
	},
	2: { // 笔记类型
		{FiltersIndex: 2, TagsIndex: 1, Text: "不限"},
		{FiltersIndex: 2, TagsIndex: 2, Text: "视频"},
		{FiltersIndex: 2, TagsIndex: 3, Text: "图文"},
	},
	3: { // 发布时间
		{FiltersIndex: 3, TagsIndex: 1, Text: "不限"},
		{FiltersIndex: 3, TagsIndex: 2, Text: "一天内"},
		{FiltersIndex: 3, TagsIndex: 3, Text: "一周内"},
		{FiltersIndex: 3, TagsIndex: 4, Text: "半年内"},
	},
	4: { // 搜索范围
		{FiltersIndex: 4, TagsIndex: 1, Text: "不限"},
		{FiltersIndex: 4, TagsIndex: 2, Text: "已看过"},
		{FiltersIndex: 4, TagsIndex: 3, Text: "未看过"},
		{FiltersIndex: 4, TagsIndex: 4, Text: "已关注"},
	},
	5: { // 位置距离
		{FiltersIndex: 5, TagsIndex: 1, Text: "不限"},
		{FiltersIndex: 5, TagsIndex: 2, Text: "同城"},
		{FiltersIndex: 5, TagsIndex: 3, Text: "附近"},
	},
}

// convertToInternalFilters 将 FilterOption 转换为内部的 internalFilterOption 列表
func convertToInternalFilters(filter FilterOption) ([]internalFilterOption, error) {
	var internalFilters []internalFilterOption

	// 处理排序依据
	if filter.SortBy != "" {
		internal, err := findInternalOption(1, filter.SortBy)
		if err != nil {
			return nil, fmt.Errorf("排序依据错误: %w", err)
		}
		internalFilters = append(internalFilters, internal)
	}

	// 处理笔记类型
	if filter.NoteType != "" {
		internal, err := findInternalOption(2, filter.NoteType)
		if err != nil {
			return nil, fmt.Errorf("笔记类型错误: %w", err)
		}
		internalFilters = append(internalFilters, internal)
	}

	// 处理发布时间
	if filter.PublishTime != "" {
		internal, err := findInternalOption(3, filter.PublishTime)
		if err != nil {
			return nil, fmt.Errorf("发布时间错误: %w", err)
		}
		internalFilters = append(internalFilters, internal)
	}

	// 处理搜索范围
	if filter.SearchScope != "" {
		internal, err := findInternalOption(4, filter.SearchScope)
		if err != nil {
			return nil, fmt.Errorf("搜索范围错误: %w", err)
		}
		internalFilters = append(internalFilters, internal)
	}

	// 处理位置距离
	if filter.Location != "" {
		internal, err := findInternalOption(5, filter.Location)
		if err != nil {
			return nil, fmt.Errorf("位置距离错误: %w", err)
		}
		internalFilters = append(internalFilters, internal)
	}

	return internalFilters, nil
}

// findInternalOption 根据筛选组索引和文本查找内部筛选选项
func findInternalOption(filtersIndex int, text string) (internalFilterOption, error) {
	options, exists := filterOptionsMap[filtersIndex]
	if !exists {
		return internalFilterOption{}, fmt.Errorf("筛选组 %d 不存在", filtersIndex)
	}

	for _, option := range options {
		if option.Text == text {
			return option, nil
		}
	}

	return internalFilterOption{}, fmt.Errorf("在筛选组 %d 中未找到文本 '%s'", filtersIndex, text)
}

// validateInternalFilterOption 验证内部筛选选项是否在有效范围内
func validateInternalFilterOption(filter internalFilterOption) error {
	// 检查筛选组索引是否有效
	if filter.FiltersIndex < 1 || filter.FiltersIndex > 5 {
		return fmt.Errorf("无效的筛选组索引 %d，有效范围为 1-5", filter.FiltersIndex)
	}

	// 检查标签索引是否在对应筛选组的有效范围内
	options, exists := filterOptionsMap[filter.FiltersIndex]
	if !exists {
		return fmt.Errorf("筛选组 %d 不存在", filter.FiltersIndex)
	}

	if filter.TagsIndex < 1 || filter.TagsIndex > len(options) {
		return fmt.Errorf("筛选组 %d 的标签索引 %d 超出范围，有效范围为 1-%d",
			filter.FiltersIndex, filter.TagsIndex, len(options))
	}

	return nil
}

type SearchAction struct {
	page *rod.Page
}

const (
	explorePageURL           = "https://www.xiaohongshu.com/explore"
	searchInputSelector      = `input.search-input, div.search-box input[type="text"], input[placeholder*="搜索"]`
	searchSubmitSelector     = `div.input-box div.input-button, button.min-width-search-icon`
	filterButtonSelector     = `div.filter`
	filterPanelSelector      = `div.filter-panel`
	searchElementWaitTimeout = 15 * time.Second
)

func NewSearchAction(page *rod.Page) *SearchAction {
	pp := page.Timeout(60 * time.Second)

	return &SearchAction{page: pp}
}

func (s *SearchAction) Search(ctx context.Context, keyword string, filters ...FilterOption) ([]Feed, error) {
	keyword = strings.TrimSpace(keyword)
	if keyword == "" {
		return nil, fmt.Errorf("搜索关键词不能为空")
	}

	allInternalFilters, err := prepareInternalFilters(filters)
	if err != nil {
		return nil, err
	}

	page := s.page.Context(ctx)

	if err := page.Navigate(explorePageURL); err != nil {
		return nil, fmt.Errorf("进入发现页失败: %w", err)
	}
	if err := page.WaitLoad(); err != nil {
		return nil, fmt.Errorf("等待发现页加载失败: %w", err)
	}

	logrus.Info("内容检索: 发现页已加载，开始真人化输入")
	if err := performHumanSearch(ctx, page, keyword); err != nil {
		return nil, fmt.Errorf("执行真人化搜索失败: %w", err)
	}
	if err := waitForSearchResultPage(ctx, page); err != nil {
		return nil, err
	}
	humanPause(450*time.Millisecond, 850*time.Millisecond)
	if err := waitForSearchState(page); err != nil {
		return nil, err
	}
	logrus.Info("内容检索: 搜索结果已加载")

	if len(allInternalFilters) > 0 {
		if err := applyHumanFilters(ctx, page, allInternalFilters); err != nil {
			return nil, err
		}
		logrus.Infof("内容检索: 已应用 %d 个筛选条件", len(allInternalFilters))
	}

	result, err := readSearchFeeds(page)
	if err != nil {
		return nil, err
	}
	if result == "" {
		return nil, errors.ErrNoFeeds
	}

	var feeds []Feed
	if err := json.Unmarshal([]byte(result), &feeds); err != nil {
		return nil, fmt.Errorf("failed to unmarshal feeds: %w", err)
	}

	return feeds, nil
}

func prepareInternalFilters(filters []FilterOption) ([]internalFilterOption, error) {
	var allInternalFilters []internalFilterOption
	for _, filter := range filters {
		internalFilters, err := convertToInternalFilters(filter)
		if err != nil {
			return nil, fmt.Errorf("筛选选项转换失败: %w", err)
		}
		allInternalFilters = append(allInternalFilters, internalFilters...)
	}

	for _, filter := range allInternalFilters {
		if err := validateInternalFilterOption(filter); err != nil {
			return nil, fmt.Errorf("筛选选项验证失败: %w", err)
		}
	}
	return allInternalFilters, nil
}

func performHumanSearch(ctx context.Context, page *rod.Page, keyword string) error {
	searchInput, err := waitForVisibleElement(ctx, page, searchInputSelector, "搜索输入框")
	if err != nil {
		return err
	}
	placeholder, err := searchInput.Attribute("placeholder")
	if err != nil {
		return fmt.Errorf("读取搜索输入框状态失败: %w", err)
	}
	if placeholder != nil && strings.Contains(*placeholder, "登录") {
		return fmt.Errorf("当前未登录，请先调用 get_login_qrcode 完成登录")
	}

	if err := humanReplaceText(page, searchInput, keyword); err != nil {
		return fmt.Errorf("输入搜索关键词失败: %w", err)
	}
	humanPause(250*time.Millisecond, 520*time.Millisecond)

	searchButton, err := waitForVisibleElement(ctx, page, searchSubmitSelector, "搜索按钮")
	if err != nil {
		return err
	}
	if err := humanClick(page, searchButton); err != nil {
		return fmt.Errorf("点击搜索按钮失败: %w", err)
	}
	return nil
}

func waitForSearchResultPage(ctx context.Context, page *rod.Page) error {
	timer := time.NewTimer(searchElementWaitTimeout)
	defer timer.Stop()

	ticker := time.NewTicker(120 * time.Millisecond)
	defer ticker.Stop()

	for {
		info, err := page.Info()
		if err == nil && strings.Contains(info.URL, "/search_result") {
			return nil
		}

		select {
		case <-ctx.Done():
			return fmt.Errorf("等待搜索结果页失败: %w", ctx.Err())
		case <-timer.C:
			return fmt.Errorf("等待搜索结果页超时")
		case <-ticker.C:
		}
	}
}

func waitForSearchState(page *rod.Page) error {
	err := page.Timeout(20 * time.Second).Wait(rod.Eval(`() => {
		const feeds = window.__INITIAL_STATE__?.search?.feeds;
		return !!feeds && (feeds.value !== undefined || feeds._value !== undefined);
	}`))
	if err != nil {
		return fmt.Errorf("等待搜索结果数据失败: %w", err)
	}
	return nil
}

func applyHumanFilters(ctx context.Context, page *rod.Page, filters []internalFilterOption) error {
	for _, filter := range filters {
		_, found, err := firstVisibleElement(page, filterPanelSelector)
		if err != nil {
			return fmt.Errorf("检查筛选面板失败: %w", err)
		}
		if !found {
			filterButton, err := waitForVisibleElement(ctx, page, filterButtonSelector, "筛选按钮")
			if err != nil {
				return err
			}
			if err := humanClick(page, filterButton); err != nil {
				return fmt.Errorf("打开筛选面板失败: %w", err)
			}
			humanPause(220*time.Millisecond, 480*time.Millisecond)

			if _, err := waitForVisibleElement(ctx, page, filterPanelSelector, "筛选面板"); err != nil {
				return err
			}
		}

		option, err := waitForVisibleFilterOption(ctx, page, filter)
		if err != nil {
			return err
		}
		if err := humanClick(page, option); err != nil {
			return fmt.Errorf("点击筛选项[%s]失败: %w", filter.Text, err)
		}
		humanPause(550*time.Millisecond, 950*time.Millisecond)
	}

	humanPause(700*time.Millisecond, 1200*time.Millisecond)
	if err := waitForSearchState(page); err != nil {
		return err
	}
	return nil
}

func waitForVisibleFilterOption(
	ctx context.Context,
	page *rod.Page,
	filter internalFilterOption,
) (*rod.Element, error) {
	timer := time.NewTimer(searchElementWaitTimeout)
	defer timer.Stop()

	ticker := time.NewTicker(120 * time.Millisecond)
	defer ticker.Stop()

	var lastErr error
	for {
		panel, found, err := firstVisibleElement(page, filterPanelSelector)
		if err != nil {
			lastErr = err
		} else if found {
			option, optionErr := findVisibleFilterOption(panel, filter)
			if optionErr == nil {
				return option, nil
			}
			lastErr = optionErr
		}

		select {
		case <-ctx.Done():
			return nil, fmt.Errorf("等待筛选项[%s]失败: %w", filter.Text, ctx.Err())
		case <-timer.C:
			if lastErr != nil {
				return nil, fmt.Errorf("等待筛选项[%s]超时: %w", filter.Text, lastErr)
			}
			return nil, fmt.Errorf("等待筛选项[%s]超时", filter.Text)
		case <-ticker.C:
		}
	}
}

func findVisibleFilterOption(panel *rod.Element, filter internalFilterOption) (*rod.Element, error) {
	groups, err := panel.Elements(`div.filters`)
	if err != nil {
		return nil, fmt.Errorf("读取筛选组失败: %w", err)
	}
	if filter.FiltersIndex > len(groups) {
		return nil, fmt.Errorf("筛选组[%d]不存在", filter.FiltersIndex)
	}

	options, err := groups[filter.FiltersIndex-1].Elements(`div.tags`)
	if err != nil {
		return nil, fmt.Errorf("读取筛选项失败: %w", err)
	}
	for _, option := range options {
		hidden, err := option.Attribute("aria-hidden")
		if err != nil {
			continue
		}
		if hidden != nil && *hidden == "true" {
			continue
		}

		visible, err := option.Visible()
		if err != nil || !visible {
			continue
		}
		text, err := option.Text()
		if err == nil && strings.TrimSpace(text) == filter.Text {
			return option, nil
		}
	}
	return nil, fmt.Errorf("未找到可见筛选项[%s]", filter.Text)
}

func waitForVisibleElement(
	ctx context.Context,
	page *rod.Page,
	selector string,
	description string,
) (*rod.Element, error) {
	timer := time.NewTimer(searchElementWaitTimeout)
	defer timer.Stop()

	ticker := time.NewTicker(120 * time.Millisecond)
	defer ticker.Stop()

	for {
		element, found, err := firstVisibleElement(page, selector)
		if err != nil {
			return nil, fmt.Errorf("查找%s失败: %w", description, err)
		}
		if found {
			return element, nil
		}

		select {
		case <-ctx.Done():
			return nil, fmt.Errorf("等待%s失败: %w", description, ctx.Err())
		case <-timer.C:
			return nil, fmt.Errorf("等待%s超时", description)
		case <-ticker.C:
		}
	}
}

func firstVisibleElement(page *rod.Page, selector string) (*rod.Element, bool, error) {
	elements, err := page.Elements(selector)
	if err != nil {
		return nil, false, err
	}
	for _, element := range elements {
		visible, err := element.Visible()
		if err != nil {
			continue
		}
		if visible {
			return element, true, nil
		}
	}
	return nil, false, nil
}

func readSearchFeeds(page *rod.Page) (string, error) {
	result, err := page.Eval(`() => {
		if (window.__INITIAL_STATE__ &&
		    window.__INITIAL_STATE__.search &&
		    window.__INITIAL_STATE__.search.feeds) {
			const feeds = window.__INITIAL_STATE__.search.feeds;
			const feedsData = feeds.value !== undefined ? feeds.value : feeds._value;
			if (feedsData) {
				return JSON.stringify(feedsData);
			}
		}
		return "";
	}`)
	if err != nil {
		return "", fmt.Errorf("读取搜索结果失败: %w", err)
	}
	return result.Value.String(), nil
}
