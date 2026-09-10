//go:build integration

package xiaohongshu

import (
	"testing"

	"github.com/go-rod/rod"
	"github.com/go-rod/rod/lib/launcher"
	"github.com/stretchr/testify/require"
)

func TestHumanCommentAndReplyActions(t *testing.T) {
	controlURL := launcher.New().Headless(true).MustLaunch()
	browser := rod.New().ControlURL(controlURL).MustConnect()
	defer browser.MustClose()

	page := browser.MustPage("about:blank")
	page.MustSetDocumentContent(`
		<!doctype html>
		<style>
			body { margin: 0; font-family: sans-serif; min-height: 2600px; }
			.input-box { position: relative; width: 520px; margin: 40px; padding: 12px; border: 1px solid #ddd; }
			.content-edit span, .content-input { display: block; width: 460px; min-height: 42px; }
			.submit, .reply { width: 120px; height: 42px; }
			.parent-comment { margin: 1250px 40px 0; width: 520px; padding: 20px; background: #f4f4f4; }
		</style>
		<div class="input-box">
			<div class="content-edit">
				<span>说点什么</span>
				<p class="content-input" contenteditable="true"></p>
			</div>
			<div class="bottom">
				<button class="submit" onclick="document.body.dataset.submitted=document.querySelector('.content-input').innerText">发送</button>
			</div>
		</div>
		<div class="parent-comment" id="comment-target">
			<div class="right">
				<div class="interactions">
					<button class="reply" onclick="document.body.dataset.replyScroll=String(window.scrollY);document.querySelector('.content-input').innerText=''">回复</button>
				</div>
			</div>
		</div>
	`)

	require.NoError(t, fillAndSubmitComment(page, "这是一条真人输入的评论"))
	submitted := page.MustElement("body").MustAttribute("data-submitted")
	require.NotNil(t, submitted)
	require.Equal(t, "这是一条真人输入的评论", *submitted)

	comment := page.MustElement("#comment-target")
	require.NoError(t, replyToCommentElement(page, comment, "这是一条逐字输入的回复"))
	replyScroll := page.MustElement("body").MustAttribute("data-reply-scroll")
	require.NotNil(t, replyScroll)
	require.NotEqual(t, "0", *replyScroll)

	inputText := page.MustElement(".content-input").MustText()
	require.Equal(t, "这是一条逐字输入的回复", inputText)
}

func TestHumanEngagementExpandAndProfileClicks(t *testing.T) {
	controlURL := launcher.New().Headless(true).MustLaunch()
	browser := rod.New().ControlURL(controlURL).MustConnect()
	defer browser.MustClose()

	page := browser.MustPage("about:blank")
	page.MustSetDocumentContent(`
		<!doctype html>
		<style>
			body { margin: 0; padding: 40px; font-family: sans-serif; }
			button, .show-more, .channel { display: block; width: 180px; height: 44px; margin: 18px; }
		</style>
		<button class="like" onclick="document.body.dataset.liked='true'">点赞</button>
		<div class="show-more" onclick="document.body.dataset.expanded='true'">展开 2 条回复</div>
		<div class="main-container">
			<li class="user side-bar-component">
				<a class="link-wrapper" href="javascript:void(0)">
					<span class="channel" onclick="document.body.dataset.profile='true'">我</span>
				</a>
			</li>
		</div>
	`)

	action := newInteractAction(page)
	require.NoError(t, action.performClick(page, ".like"))
	liked := page.MustElement("body").MustAttribute("data-liked")
	require.NotNil(t, liked)
	require.Equal(t, "true", *liked)

	clicked, skipped := clickShowMoreButtonsSmart(page, 10)
	require.Equal(t, 1, clicked)
	require.Zero(t, skipped)
	expanded := page.MustElement("body").MustAttribute("data-expanded")
	require.NotNil(t, expanded)
	require.Equal(t, "true", *expanded)

	require.NoError(t, clickProfileLink(page))
	profile := page.MustElement("body").MustAttribute("data-profile")
	require.NotNil(t, profile)
	require.Equal(t, "true", *profile)
}
