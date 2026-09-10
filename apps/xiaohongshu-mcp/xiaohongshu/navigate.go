package xiaohongshu

import (
	"context"
	"fmt"
	"time"

	"github.com/go-rod/rod"
)

type NavigateAction struct {
	page *rod.Page
}

func NewNavigate(page *rod.Page) *NavigateAction {
	return &NavigateAction{page: page}
}

func (n *NavigateAction) ToExplorePage(ctx context.Context) error {
	page := n.page.Context(ctx)

	if err := page.Navigate("https://www.xiaohongshu.com/explore"); err != nil {
		return fmt.Errorf("打开探索页失败: %w", err)
	}
	if err := page.WaitLoad(); err != nil {
		return fmt.Errorf("等待探索页加载失败: %w", err)
	}
	if _, err := page.Element(`div#app`); err != nil {
		return fmt.Errorf("探索页未正常加载: %w", err)
	}

	return nil
}

func (n *NavigateAction) ToProfilePage(ctx context.Context) error {
	page := n.page.Context(ctx)

	// First navigate to explore page
	if err := n.ToExplorePage(ctx); err != nil {
		return err
	}

	humanPause(450*time.Millisecond, 900*time.Millisecond)

	if err := clickProfileLink(page); err != nil {
		return err
	}

	// Wait for navigation to complete
	humanPause(500*time.Millisecond, 1000*time.Millisecond)
	return page.WaitLoad()
}

func clickProfileLink(page *rod.Page) error {
	profileLink, err := page.Element(`div.main-container li.user.side-bar-component a.link-wrapper span.channel`)
	if err != nil {
		return fmt.Errorf("未找到个人主页入口: %w", err)
	}
	if err := humanClick(page, profileLink); err != nil {
		return fmt.Errorf("真人化点击个人主页入口失败: %w", err)
	}
	return nil
}
