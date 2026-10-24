(function(){
  // mobile sidebar
  var sb=document.getElementById('sidebar'),bd=document.getElementById('backdrop'),tg=document.getElementById('menuToggle');
  function tgl(){sb.classList.toggle('show');bd.classList.toggle('show');}
  if(tg){tg.addEventListener('click',tgl);bd.addEventListener('click',tgl);}
  // confirm dialogs: <form data-confirm="Are you sure?">
  document.addEventListener('submit',function(e){
    var m=e.target.getAttribute('data-confirm');
    if(m&&!window.confirm(m)){e.preventDefault();}
  });
  // print buttons
  document.querySelectorAll('[data-print]').forEach(function(b){b.addEventListener('click',function(){window.print();});});
  // auto-dismiss success alerts
  setTimeout(function(){document.querySelectorAll('.alert-success').forEach(function(a){a.classList.remove('show');setTimeout(function(){a.remove();},300);});},5000);
})();
