// 公共工具脚本
// 导航激活、加载提示、简易弹窗
(function(window, $){
    // 自动高亮侧边栏菜单
    function activateNav(){
        try{
            const path = window.location.pathname || '';
            const parts = path.split('/');
            const file = parts[parts.length-1] || 'index.html';
            $('.sidebar .nav-link').each(function(){
                const href = $(this).attr('href') || '';
                if(href.endsWith(file)){
                    $(this).addClass('active');
                }else{
                    $(this).removeClass('active');
                }
            });
        }catch(e){ console.error('activateNav error', e); }
    }

    // 加载控件（显示在顶部按钮旁）
    function showLoading(text){
        const $el = $('#loading');
        if($el.length===0) return;
        $el.removeClass('d-none').html('<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> ' + (text||'运行中...'));
    }
    function hideLoading(){
        const $el = $('#loading');
        if($el.length===0) return;
        $el.addClass('d-none').html('');
    }

    // 简易提示（success | warn | error）
    function showTip(msg, type='success', timeout=4000){
        const color = type==='success'? 'bg-success': (type==='error' ? 'bg-danger' : 'bg-warning');
        const $box = $('<div>').addClass('toast align-items-center text-white '+color+' border-0').attr('role','alert');
        const $inner = $('<div>').addClass('d-flex');
        $inner.append($('<div>').addClass('toast-body').text(msg));
        $inner.append($('<button>').addClass('btn-close btn-close-white me-2 m-auto').attr({'data-bs-dismiss':'toast','aria-label':'Close'}));
        $box.append($inner);
        const $container = $('#toastContainer');
        if($container.length===0){
            const cont = $('<div id="toastContainer" aria-live="polite" aria-atomic="true" style="position:fixed; top:20px; right:20px; z-index:9999;"></div>');
            $('body').append(cont);
        }
        $('#toastContainer').append($box);
        setTimeout(()=>{ $box.fadeOut(300, ()=> $box.remove()); }, timeout);
    }

    // 导出到全局
    window.activateNav = activateNav;
    window.showLoading = showLoading;
    window.hideLoading = hideLoading;
    window.showTip = showTip;

    // 页面加载自动激活
    $(function(){ activateNav(); });
})(window, jQuery);
