/* Shared product identity and stale-response protection for the three workspaces. */
(()=>{'use strict';let product=null;const sequence={};
 try{product=sessionStorage.getItem('seedance-product-flow')||null}catch{}
 window.ProductFlow={
  get:()=>product,
  set(id){product=typeof id==='string'&&id.trim()?id:null;try{if(product)sessionStorage.setItem('seedance-product-flow',product);else sessionStorage.removeItem('seedance-product-flow')}catch{}return product},
  begin(channel){const id=(sequence[channel]||0)+1;sequence[channel]=id;const selected=product;return ()=>sequence[channel]===id&&product===selected;}
 };
})();
