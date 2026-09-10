
(function($) {
	$(function() {
		$('#s').autocomplete({
			source: '/wp/wp-content/plugins/search-autocomplete/includes/tags.php',
			minLength: 3,
      select: function(event, ui) {
      	if (ui.item.url !== 'none') {
        	location = ui.item.url;
        } else {
      		$(this).val(ui.item.label);
        }
      }
		});
	});
})(jQuery);
