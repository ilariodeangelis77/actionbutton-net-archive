var ABDN_UI = function(){
	
	var that 	= {},
		su 		= null;
	
	that.Support = {
		_fixed: function() {
			var container = document.body;
			
			if (document.createElement && container && container.appendChild && container.removeChild) {
				var el = document.createElement('div');
				
				if (!el.getBoundingClientRect) return null;
				
				el.innerHTML = 'x';
				el.style.cssText = 'position:fixed;top:100px;';
				container.appendChild(el);
				
				var originalHeight = container.style.height,
				originalScrollTop = container.scrollTop;
				
				container.style.height = '3000px';
				container.scrollTop = 500;
				
				var elementTop = el.getBoundingClientRect().top;
				container.style.height = originalHeight;
				
				var isSupported = (elementTop === 100);
				container.removeChild(el);
				container.scrollTop = originalScrollTop;
				
				return isSupported;
			}
			return null;
		},
		_ios: function(){
			var ua = navigator.userAgent;
			return ua.match(/iPhone/i) || ua.match(/iPod/i) || ua.match(/iPad/i);
		},
		init: function() {
			for(var i in this) {
				if(i.substring(0,1) == '_') {
					this[i.replace('_','')] = this[i]();
				}
			}
		}
	};
	
	that.iOS = function() {
		if (this.Support.ios) {
			$('body').addClass('platform-ios');
			if ($('body').hasClass('home')) {
				document.title = 'actionbutton';
			}
		};
	};
	
	that.Toggleable = function(params) {
		
		var tp = {
			ctx: null,
			hd: null,
			tg: null,
			speed: 150,
			tclass: 'toggleable',
			tgclass: 'toggleable-target',
			opclass: 'toggleable-open',
			hidecond: true,
			title: '',
			text: '',
			textopen: ''
		};
		$.extend(true, tp, params);
		
		var tog = {};
		
		tog.hide = function() {
			$(tp.hd, tp.ctx).slideUp(tp.speed, function(){
				su.recalc();
			});
			$(tp.ctx).removeClass(tp.opclass);
			
			if (tp.text.length > 0) {
			  $(tp.tg, tp.ctx).text(tp.text);
			}
		};
		
		tog.show = function()
		{
			$(tp.hd, tp.ctx).slideDown(tp.speed, function(){
				$.scrollTo($(tp.tg, tp.ctx), 250, {});
				su.recalc();
			});
			$(tp.ctx).addClass(tp.opclass);
			
			if (tp.textopen.length > 0) {
			  $(tp.tg, tp.ctx).text(tp.textopen);
			}
		};
		
		tog._setEvents = function()
		{
			var self = this;
			$(tp.ctx).addClass(tp.tclass);
			$(tp.tg, tp.ctx).click( function(e){
				e.preventDefault();
				if( $(tp.hd, tp.ctx).is(':hidden') ) self.show();
				else self.hide();
			}).attr('title', tp.title).addClass(tp.tgclass);
			
			if(tp.hidecond) {
			  this.hide();
        if (tp.text.length > 0) {
          $(tp.tg, tp.ctx).text(tp.text);
        }
			}
			else {
			  $(tp.ctx).addClass(tp.opclass);
			  if (tp.textopen.length > 0) {
			    $(tp.tg, tp.ctx).text(tp.textopen);
			  }
			}
		};
		
		tog.init = function() {
			this._setEvents();
		}
		
		tog.init();
		
		return tog;
	};
	
	that.Search = function() {
		
		var sp = {
			s: '#searchform',
			elemfocus: false,
			hover: false
		};
		
		var checkState = function() {
			if (sp.elemfocus || sp.hover) {
				sp.s.addClass('focused');
			}
			else {
				sp.s.removeClass('focused');
			}
		};
				
		sp.s = $(sp.s);
		sp.s
			.mouseover(function(){ sp.hover = true; checkState(); })
			.mouseout(function(){ sp.hover = false; checkState(); });
		$('input', sp.s)
			.focus(function(){ sp.elemfocus = true; checkState(); })
			.blur(function(){ sp.elemfocus = false; checkState(); });
	};
	
	that.ModeSwitcher = function() {
		var options = ['dark', 'light', 'unicorn', 'ziggurat'],
			omake   = ['omake-mode'],
			index 	= options.length - 1,
			cn		= 'abdn_display_mode',
			chTimes	= 0,
			egg		= 10,
			omakeln = 108,
			sf		= null;
		
		if (!!$.cookie(cn)) {
			index = $.cookie(cn) - 1;
		}
		
		var switchMode = function() {
			var newIndex = index+1;
			if (newIndex >= options.length) newIndex = 0;
			
			$('body').removeClass(options[index] + '-mode')
				.removeClass(omake.join(' '))
				.addClass(options[newIndex] + '-mode');
			
			index = newIndex;
			$.cookie(cn, index);
			
			chTimes++;
			if (chTimes > omakeln) {
				$('body').removeClass(options[newIndex] + '-mode')
					.addClass(omake[Math.floor(Math.random() * omake.length)]);
				chTimes = 0;
				
				if (!!sf) {
					sf.stop();
					sf.setPlayable(false);
				}
			} 
			else if (index == 1 && chTimes % egg == 0) {
				if (!sf) {
					sf = new Starfield();
				}
				sf.setPlayable(true);
				sf.play();
				chTimes = 0;
			}
			else {
				if (!!sf) {
					sf.stop();
					sf.setPlayable(false);
				}
			}
		};
		
		$('.action-button-button').click(switchMode);
		switchMode();
	};
	
	that.Fixer = function(selectors) {
		if (!this.Support.fixed) {
			var FixHack = function(){
				var newTop = $(window).scrollTop();
				var offset = -38;
				for (var i in selectors) {
					if (i == 'length') continue;
					var el = $(selectors[i]);
					if (!!el.css('top')) {
						el.css({
							position: 'absolute',
							top: (newTop + offset) + 'px'
						});
					}
					else if (!!el.css('bottom')) {
						var newBottom = newTop + $(window).height() - el.height();
						el.css({
							position: 'absolute',
							top: newBottom + 'px'
						});
					}
				}
			};
			$(window).scroll(FixHack);
			FixHack();
		}
	};
	
	that.ScrollUpdate = function() {
		var data = {
			x:	 	$('body').data('bgx').split(','),
			top: 	$('body').data('bgtop').split(','),
			bottom: $('body').data('bgbottom').split(',')
		};
		
		var docHeight = function(){
			return $('body').get(0).clientHeight - $(window).height();
		};
		
		var scrollPos = function() {
			return st / dh;
		};
		
		var calculating = false;
		
		var recalcBgPosition = function() {
			if (!!that.Support && !!that.Support.ios) return;
			calculating = true;
			var pos 	= [],
				diff 	= 0;
			for (var i = 0; i < data.x.length; i++) {
				var t = parseInt(data.top[i], 10),
					b = parseInt(data.bottom[i], 10);
				diff = t + ((b - t) * scrollPos());
				pos.push(data.x[i] + "% " + diff + "%");
			}
			$('body').css('background-position', pos.join(', '));
			calculating = false;
		};
		
		var limit 	= $('#main').offset().top * 0.8,
			haslogo = 'access-has-logo',
			dh 		= docHeight(),
			st		= null;
		
		$(window).scroll(function(){
			st = $(window).scrollTop();
			if (st > limit) {
				$('body').addClass(haslogo);
			}
			else {
				$('body').removeClass(haslogo);
			}
			if (!calculating) recalcBgPosition();
		});
		
		var that = {};
		that.recalc = function() {
			st = $(window).scrollTop();
			if (!calculating) dh = docHeight();
			if (!calculating) recalcBgPosition();
		};
		return that;
	};
	
	that.Archive = function() {
		
		var ShowId = function(id) {
			$('.review-archive-block').not('#'+id).slideUp();
			$('#'+id).slideDown();
		};
		
		if ($('body').hasClass('page-id-175')) {
			$('#review-sort a').click(function(e){
				e.preventDefault();
				ShowId(this.href.split('#')[1]);
			});
			ShowId('date-sort');
		}
	};
	
	that.init = function() {
		this.Support.init();
		this.iOS();
		this.Search();
		this.ModeSwitcher();
		su = this.ScrollUpdate();
		this.Archive();
		
		var toFix = ['#access'];
		this.Fixer(toFix);
	};
	
	that.init();
	
	return that;
	
};

$(document).ready( function(){
	
	var ABDN_UI_Obj = new ABDN_UI();
	
	var Comments = new ABDN_UI_Obj.Toggleable({
		ctx: '#comment-container',
		hd: '#comments',
		tg: '#comment-container-title',
		title: 'Toggle comments',
		opclass: 'toggleable-open',
		text: 'Show comments',
		textopen: 'Hide comments',
		hidecond: ( window.location.hash.indexOf('#comment-') < 0 ) && ( window.location.hash.indexOf('#comments') < 0 ) && ( window.location.hash.indexOf('#respond') < 0 )
	});
	
	var ArchToggle = new ABDN_UI_Obj.Toggleable({
		ctx: '#archives',
		hd: 'ul',
		tg: 'h3',
		title: 'Toggle monthly archives',
		text: 'Show monthly archives',
		textopen: 'Hide monthly archives',
		opclass: 'toggleable-open'
	});
	
});