
from flask import Flask, render_template, request, send_from_directory, jsonify
from urllib.parse import urlparse
from scrapers.ernest_jones import handle_ernest_jones
from scrapers.shaneco import handle_shane_co
from scrapers.fhinds import handle_fhinds
from scrapers.gabriel import handle_gabriel
from scrapers.hsamuel import handle_h_samuel
from scrapers.kay import handle_kay
from scrapers.jared import handle_jared
from scrapers.tiffany import handle_tiffany
#==========================================#
from scrapers.kayoutlet import handle_kayoutlet
from scrapers.zales import handle_zales
from scrapers.helzberg import handle_helzberg
from scrapers.rosssimons import handle_rosssimons
from scrapers.peoplesjewellers import handle_peoplesjewellers
from scrapers.fraserhart import handle_fraserhart
from scrapers.fields import handle_fields
from scrapers.warrenjames import handle_warrenjames
from scrapers.goldsmiths import handle_goldsmiths
from scrapers.thediamondstore import handle_thediamondstore
from scrapers.prouds import handle_prouds
from scrapers.goldmark import handle_goldmark
from scrapers.anguscoote import handle_anguscoote
from scrapers.bash import handle_bash
from scrapers.shiels import handle_shiels
from scrapers.mazzucchellis import handle_mazzucchellis
from scrapers.hoskings import handle_hoskings
from scrapers.hardybrothers import handle_hardybrothers
from scrapers.zamels import handle_zamels
from scrapers.wallacebishop import handle_wallacebishop
from scrapers.bevilles import handle_bevilles
from scrapers.michaelhill import handle_michaelhill
from scrapers.apart import handle_apart
from scrapers.macys import handle_macys
from scrapers.jcpenney import handle_jcpenney
from scrapers.fredmeyer import handle_fredmeyer
from scrapers.beaverbrooks import handle_beaverbrooks
#############################################################################################################
            #stage 2
#############################################################################################################
from scrapers.finks import handle_finks
from scrapers.smilingrocks import handle_smilingrocks
from scrapers.bluenile import handle_bluenile
from scrapers.benbridge import handle_benbridge
from scrapers.hannoush import handle_hannoush
from scrapers.jcojewellery import handle_jcojewellery
from scrapers.diamonds import handle_77diamonds
from scrapers.reeds import handle_reeds
from scrapers.walmart import handle_walmart
#############################################################################################################
from scrapers.armansfinejewellery import handle_armansfinejewellery
from scrapers.jacquefinejewellery import handle_jacquefinejewellery
from scrapers.medleyjewellery import handle_medleyjewellery
from scrapers.cullenjewellery import handle_cullenjewellery
from scrapers.grahams import handle_grahams
from scrapers.larsenjewellery import handle_larsenjewellery
from scrapers.ddsdiamonds import handle_ddsdiamonds
from scrapers.garenjewellery import handle_garenjewellery
from scrapers.stefandiamonds import handle_stefandiamonds
from scrapers.goodstoneinc import handle_goodstoneinc
from scrapers.natashaschweitzer import handle_natasha
from scrapers.sarahandsebastian import handle_sarahandsebastian
from scrapers.moissanite import handle_moissanite
from scrapers.daimondcollection import handle_diamondcollection
from scrapers.cushlawhiting import handle_cushlawhiting
from scrapers.cerrone import handle_cerrone
from scrapers.briju import handle_briju
from scrapers.histoiredor import handle_histoiredor
from scrapers.marcorian import handle_marcorian
from scrapers.klenotyaurum import handle_klenotyaurum

from scrapers.stroilioro import handle_stroilioro
from scrapers.americanswiss import handle_americanswiss
from scrapers.mariemass import handle_mariemass
from scrapers.mattioli import handle_mattioli
from scrapers.pomellato import handle_pomellato
from scrapers.dior import handle_dior
from scrapers.bonnie import handle_bonnie

########################################### 24/07 ################################################################## 
from scrapers.diamondsfactory import handle_diamondsfactory
from scrapers.davidmarshalllondon import handle_davidmarshalllondon
from scrapers.monicavinader import handle_monicavinader
from scrapers.boodles import handle_boodles
from scrapers.mariablack import handle_mariablack
from scrapers.londonjewelers import handle_londonjewelers
from scrapers.fernandojorge import handle_fernandojorge
from scrapers.pandora import handle_pandora
from scrapers.daisyjewellery import handle_daisyjewellery
from scrapers.missoma import handle_missoma
from scrapers.astleyclarke import handle_astleyclarke
from scrapers.edgeofember import handle_edgeofember
from scrapers.mateo import handle_mateo
from scrapers.bybonniejewelry import handle_bybonniejewelry
################################################ 25/04 ############################################################# 
from scrapers.tacori import handle_tacori
from scrapers.vancleefarpels import handle_vancleefarpels
from scrapers.davidyurman import handle_davidyurman
from scrapers.chopard import handle_chopard

from scrapers.jonehardy import handle_jonehardy
from scrapers.anitako import handle_anitako
from scrapers.jennifermeyer import handle_jennifermeyer
from scrapers.jacquieaiche import handle_jacquieaiche
from scrapers.jacobandco import handle_jacobandco
from scrapers.ferkos import handle_ferkos
from scrapers.heartsonfire import handle_heartsonfire

################################################## 26 /04 ###########################################################
from scrapers.chanel import handle_chanel
from scrapers.buccellati import handle_buccellati
from scrapers.harrywinston import handle_harrywinston

from scrapers.jadetrau import handle_jadetrau
from scrapers.vrai import handle_vrai
from scrapers.stephaniegottlieb import handle_stephaniegottlieb
from scrapers.marcobicego import handle_marcobicego
from scrapers.ringconcierge import handle_ringconcierge
from scrapers.eastwestgemco import handle_eastwestgemco
from scrapers.facets import handle_facets
from scrapers.birks import handle_birks
from scrapers.boochier import handle_boochier

############################################# 28/04  ########################################################
from scrapers.graff import handle_graff
from scrapers.mejuri import handle_mejuri
from scrapers.boucheron import handle_boucheron
from scrapers.chaumet import handle_chaumet
from scrapers.brilliantearth import handle_brilliantearth
from scrapers.forevermark import handle_forevermark
from scrapers.louisvuitton import handle_louisvuitton

from scrapers.piaget import handle_piaget
from scrapers.harrods import handle_harrods
from scrapers.cartier import handle_cartier
# from scrapers.hannoush import handle_hannoush
from scrapers.bulgari import handle_bulgari
from scrapers.laurenbjewelry1 import handle_laurenbjewelry1
from scrapers.ajaffe import handle_ajaffe


#############################################################################################################

import asyncio
from flask_cors import CORS
from utils import get_public_ip,log_event
from limit_checker import check_monthly_limit
import json
from database import reset_scraping_limit,get_scraping_settings,get_all_scraped_products
from ip_tracker import generate_unique_id,insert_scrape_log,update_scrape_status
app = Flask(__name__)
CORS(app)
#############################################################################################################
import logging
import os
os.makedirs("logs", exist_ok=True)

# File to store request count
request_count_file = "logs/proxy_request_count.txt"

# Read request count from file or initialize it
if os.path.exists(request_count_file):
    with open(request_count_file, "r") as f:
        try:
            request_count = int(f.read().strip())
        except ValueError:
            request_count = 0
else:
    request_count = 0

def log_and_increment_request_count():
    """Increment and log the number of requests made via proxy."""
    global request_count
    request_count += 1
    with open(request_count_file, "w") as f:
        f.write(str(request_count))
    logging.info(f"Total requests via proxy: {request_count}")


        
#############################################################################################################
# Load JSON data
def load_websites():
    with open("retailer.json", "r") as file:
        return json.load(file)["websites"]

@app.route("/")
def main():
    websites = load_websites()
    
    return render_template("main.html", websites=websites)

@app.route('/fetch', methods=['POST'])
def fetch_data():
    # Check the daily limit
    if not check_monthly_limit():
        return jsonify({"400": "Daily limit reached. Scraping is disabled."}), 400
   
    # Get URL and pagination details
    url = request.form.get('url')
    max_pages = int(request.form.get('maxPages', 1))  # Ensure max_pages is an integer


    # print("Final URL:", final_url)
    domain = urlparse(url).netloc.lower()
    
    # scrape_id = generate_unique_id(url)
    # insert_scrape_log(scrape_id, url, 'active')
    print(domain)
    logging.info(f"Processing request for domain: {domain}")

    # Increment and log request count
    log_and_increment_request_count()

    # Check domain and call corresponding handler function
    if 'www.jared.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_jared(url, max_pages))    
    elif 'www.kay.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_kay(url, max_pages))    
    elif 'www.fhinds.co.uk' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_fhinds(url, max_pages))
    elif 'www.ernestjones.co.uk' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_ernest_jones(url, max_pages))
    elif 'www.gabrielny.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_gabriel(url, max_pages)) 
    elif 'www.hsamuel.co.uk' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_h_samuel(url, max_pages)) 
    elif 'www.tiffany.co.in' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_tiffany(url, max_pages)) 
    elif 'www.shaneco.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_shane_co(url, max_pages))
#======================================================================#
    elif 'www.kayoutlet.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_kayoutlet(url, max_pages)) 
    elif 'www.zales.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_zales(url, max_pages))       
    elif 'www.helzberg.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_helzberg(url, max_pages))
    elif 'www.ross-simons.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_rosssimons(url, max_pages))
    elif 'www.peoplesjewellers.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_peoplesjewellers(url, max_pages))  
    elif 'www.fraserhart.co.uk' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_fraserhart(url, max_pages)) 
    elif 'www.fields.ie' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_fields(url, max_pages))
    elif 'www.warrenjames.co.uk' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_warrenjames(url, max_pages))
    elif 'www.goldsmiths.co.uk' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_goldsmiths(url, max_pages))
    elif 'www.thediamondstore.co.uk' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_thediamondstore(url, max_pages))
    elif 'www.prouds.com.au' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_prouds(url, max_pages)) 
    elif 'www.goldmark.com.au' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_goldmark(url, max_pages))
    elif 'www.anguscoote.com.au' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_anguscoote(url, max_pages))   
    elif 'bash.com' in domain:  
        base64_encoded, filename, file_path = asyncio.run(handle_bash(url, max_pages)) 
    elif 'www.shiels.com.au' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_shiels(url, max_pages)) 
    elif 'mazzucchellis.com.au' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_mazzucchellis(url, max_pages)) 
    elif 'hoskings.com.au' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_hoskings(url, max_pages)) 
    elif 'www.hardybrothers.com.au' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_hardybrothers(url, max_pages))
    elif 'www.zamels.com.au' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_zamels(url, max_pages))
    elif 'www.wallacebishop.com.au' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_wallacebishop(url, max_pages)) 
    elif 'www.bevilles.com.au' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_bevilles(url, max_pages))    
    elif 'www.michaelhill.com.au' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_michaelhill(url, max_pages))
    elif 'www.apart.eu' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_apart(url, max_pages))
    elif 'www.macys.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_macys(url, max_pages))
    elif 'www.jcpenney.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_jcpenney(url, max_pages))
    elif 'www.fredmeyerjewelers.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_fredmeyer(url, max_pages))
    elif 'www.beaverbrooks.co.uk' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_beaverbrooks(url, max_pages)) 
        
######################################### 21/04 ####################################################################                                                                                          
    elif 'www.finks.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_finks(url, max_pages))  
    elif 'smilingrocks.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_smilingrocks(url, max_pages))
    elif 'www.bluenile.com' in domain: 
        base64_encoded, filename, file_path = asyncio.run(handle_bluenile(url, max_pages)) 
    elif 'www.benbridge.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_benbridge(url, max_pages)) 
    elif 'www.hannoush.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_hannoush(url, max_pages)) 
    elif 'www.jcojewellery.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_jcojewellery(url, max_pages))
    elif 'www.77diamonds.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_77diamonds(url, max_pages))
    elif 'www.reeds.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_reeds(url, max_pages))
    elif 'www.walmart.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_walmart(url, max_pages))     
############################################# 22/04 ################################################################               
    elif 'armansfinejewellery.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_armansfinejewellery(url, max_pages)) 
    elif 'jacquefinejewellery.com.au' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_jacquefinejewellery(url, max_pages))
    elif 'medleyjewellery.com.au' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_medleyjewellery(url, max_pages))
    elif 'cullenjewellery.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_cullenjewellery(url, max_pages)) 
    elif 'www.grahams.com.au' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_grahams(url, max_pages))
    elif 'www.larsenjewellery.com.au' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_larsenjewellery(url, max_pages))  
    elif 'ddsdiamonds.com.au' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_ddsdiamonds(url, max_pages))
    elif 'www.garenjewellery.com.au' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_garenjewellery(url, max_pages))
    elif 'stefandiamonds.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_stefandiamonds(url, max_pages))
    elif 'www.goodstoneinc.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_goodstoneinc(url, max_pages))                             
    elif 'natashaschweitzer.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_natasha(url, max_pages))
    elif 'www.sarahandsebastian.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_sarahandsebastian(url, max_pages))
    elif 'tmcfinejewellers.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_moissanite(url, max_pages))
    elif 'diamondcollective.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_diamondcollection(url, max_pages))
    elif 'cushlawhiting.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_cushlawhiting(url, max_pages))
    elif 'cerrone.com.au' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_cerrone(url, max_pages))     
#############################################################################################################
    elif 'www.briju.pl' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_briju(url, max_pages))
    elif 'www.histoiredor.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_histoiredor(url, max_pages))
    elif 'www.marc-orian.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_marcorian(url, max_pages))
    elif 'www.klenotyaurum.cz' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_klenotyaurum(url, max_pages))       
    elif 'www.stroilioro.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_stroilioro(url, max_pages)) 
    elif 'bash.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_americanswiss(url, max_pages))  
    elif 'mariemas.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_mariemass(url, max_pages))
    elif 'mattioli.it' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_mattioli(url, max_pages))
    elif 'www.pomellato.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_pomellato(url, max_pages))
    elif 'www.dior.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_dior(url, max_pages))
    elif 'bybonniejewelry.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_bonnie(url, max_pages))              
                        
########################################### 24/07 ################################################################## 
    elif 'www.diamondsfactory.co.uk' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_diamondsfactory(url, max_pages)) 
    elif 'www.davidmarshalllondon.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_davidmarshalllondon(url, max_pages))
    elif 'www.monicavinader.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_monicavinader(url, max_pages))        
    elif 'www.boodles.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_boodles(url, max_pages))
    elif 'www.maria-black.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_mariablack(url, max_pages))    
    elif 'www.londonjewelers.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_londonjewelers(url, max_pages))
    elif 'fernandojorge.co.uk' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_fernandojorge(url, max_pages)) 
    elif 'us.pandora.net' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_pandora(url, max_pages)) 
    elif 'www.daisyjewellery.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_daisyjewellery(url, max_pages)) 
    elif 'www.missoma.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_missoma(url, max_pages)) 
    elif 'bybonniejewelry.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_bybonniejewelry(url, max_pages))
    elif 'mateonewyork.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_mateo(url, max_pages))
    elif 'edgeofember.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_edgeofember(url, max_pages))
    elif 'www.astleyclarke.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_astleyclarke(url, max_pages))  
################################################ 25/04 ############################################################# 
    elif 'www.tacori.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_tacori(url, max_pages))
    elif 'www.vancleefarpels.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_vancleefarpels(url, max_pages))
    elif 'www.davidyurman.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_davidyurman(url, max_pages))
    elif 'www.chopard.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_chopard(url, max_pages)) 
    elif "johnhardy.com" in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_jonehardy(url, max_pages))
    elif "www.anitako.com" in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_anitako(url, max_pages))
    elif "jennifermeyer.com" in domain: 
        base64_encoded, filename, file_path = asyncio.run(handle_jennifermeyer(url, max_pages))
    elif "jacquieaiche.com" in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_jacquieaiche(url, max_pages))
    elif "jacobandco.shop" in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_jacobandco(url, max_pages))
    elif "ferkosfinejewelry.com" in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_ferkos(url, max_pages))
    elif "www.heartsonfire.com" in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_heartsonfire(url, max_pages))
         
                                     
################################################### 26 /04 ##########################################################
    elif 'www.chanel.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_chanel(url, max_pages)) 
    elif 'www.buccellati.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_buccellati(url, max_pages))
    elif 'www.harrywinston.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_harrywinston(url, max_pages))  
    
    elif "jadetrau.com" in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_jadetrau(url, max_pages))
    elif "www.vrai.com" in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_vrai(url, max_pages))
    elif "stephaniegottlieb.com" in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_stephaniegottlieb(url, max_pages))
    elif "marcobicego.com" in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_marcobicego(url, max_pages))
    elif "ringconcierge.com" in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_ringconcierge(url, max_pages))
    elif "eastwestgemco.com" in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_eastwestgemco(url, max_pages))
    elif "64facets.com" in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_facets(url, max_pages))
    elif "boochier.com" in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_boochier(url, max_pages))
    elif "www.birks.com" in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_birks(url, max_pages))
    
           
############################################# 28/04  ################################################################ 
    elif 'www.graff.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_graff(url, max_pages))
    elif 'mejuri.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_mejuri(url, max_pages))  
    elif 'www.boucheron.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_boucheron(url, max_pages)) 
    elif 'www.chaumet.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_chaumet(url, max_pages)) 
    elif 'www.brilliantearth.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_brilliantearth(url, max_pages))
    elif 'www.forevermark.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_forevermark(url, max_pages))
    elif 'eu.louisvuitton.com' in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_louisvuitton(url, max_pages))
    elif "www.piaget.com" in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_piaget(url, max_pages))
    elif "www.harrods.com" in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_harrods(url, max_pages))
    elif "www.cartier.com" in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_cartier(url, max_pages))
    # elif "www.hannoush.com" in domain:
    #     base64_encoded, filename, file_path = asyncio.run(handle_hannoush(url, max_pages))
    elif "www.bulgari.com" in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_bulgari(url, max_pages))
    elif "in.louisvuitton.com" in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_laurenbjewelry1(url, max_pages))
    elif "ajaffe.com" in domain:
        base64_encoded, filename, file_path = asyncio.run(handle_ajaffe(url, max_pages))
    # elif "www.laurenbjewelry.com" in domain:
    #     base64_encoded, filename, file_path = asyncio.run(handle_laurenbjewelry(url, max_pages))
                            
#############################################################################################################
    else:
        log_event(f"Unknown website attempted: {domain}")
        return jsonify({"error": "Unknown website"}), 200
    
    # Return file download link or error message
    if filename:
        # update_scrape_status(scrape_id, 'inactive')
        log_event(f"Successfully scraped {domain}. File generated: {filename}")
        return jsonify({'file': base64_encoded, 'filename': filename, 'filepath': file_path})
    else:
        # update_scrape_status(scrape_id, 'error')
        log_event(f"Scraping failed for {domain}")
        return jsonify({"error": "File generation failed"}), 500


#############################################################################################################
#############################################################################################################

@app.route("/reset-limit", methods=["GET"])
def reset_limit_route():
    result = reset_scraping_limit()
    return (jsonify(result), 200) if not result.get("error") else (jsonify(result), 500)


@app.route("/get_data")
def get_data():
    return jsonify(get_scraping_settings())



@app.route("/get_products", methods=["GET"])
def get_products():
    return jsonify(get_all_scraped_products())

@app.route("/product_view")
def product_view():
    
    products = get_all_scraped_products()
    # print(products)
    # print(type(products))
    return render_template("product_view.html", products=products)

#############################################################################################################
#############################################################################################################
if __name__ == '__main__':
    # app.run(debug=True)
    app.run(host="0.0.0.0", port=8001)