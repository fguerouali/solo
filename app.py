from flask import Flask, request, jsonify, render_template_string
import gspread
import os
from datetime import datetime

app = Flask(__name__)

# --- CONFIGURATION (À REMPLACER AVEC VOS DONNÉES) ---
# L'ID de votre Google Sheet (extrait de son URL)
SPREADSHEET_ID = "1gRyx4jhmrPmOuZWxS7FRMXNlWTM4GRJXoT4s2bRWuy4" 
STOCK_ALERT_THRESHOLD = 5.0 

# Récupération des identifiants Gspread depuis l'environnement Render
# Sur Render, vous créerez une variable d'environnement 'GSPREAD_CREDENTIALS' 
# où vous collerez le contenu JSON de votre fichier de compte de service.
try:
    GSPREAD_CREDENTIALS = os.environ.get('GSPREAD_CREDENTIALS')
    if not GSPREAD_CREDENTIALS:
        raise ValueError("La variable d'environnement GSPREAD_CREDENTIALS n'est pas définie.")

    # Convertir la chaîne JSON en objet Python
    CREDENTIALS = json.loads(GSPREAD_CREDENTIALS)
    gc = gspread.service_account_from_dict(CREDENTIALS)
    sh = gc.open_by_key(SPREADSHEET_ID)

except Exception as e:
    # Ceci s'affichera si l'application ne peut pas se connecter à Sheets
    print(f"Erreur de connexion Gspread: {e}")
    sh = None # Défini à None si la connexion échoue

# --- FONCTIONS UTILITAIRES DE GESTION SHEETS ---

def get_worksheet(title):
    """Retourne la feuille de calcul par son titre."""
    if sh is None:
        raise ConnectionError("Application non connectée à Google Sheets.")
    return sh.worksheet(title)

def load_data(worksheet_title):
    """Charge toutes les données d'une feuille de calcul sous forme de liste de dictionnaires."""
    try:
        wks = get_worksheet(worksheet_title)
        # get_all_records() suppose que la première ligne contient les en-têtes (colonnes)
        return wks.get_all_records()
    except Exception as e:
        print(f"Erreur de chargement des données de {worksheet_title}: {e}")
        return []

def get_inventory_dict(inventory_list):
    """Convertit la liste d'inventaire en dictionnaire pour un accès facile (Ingrédient -> {data})."""
    # Key: Nom de l'ingrédient, Value: {Quantite, Unite, Prix_Unitaire}
    inventory_dict = {}
    for item in inventory_list:
        try:
            inventory_dict[item['Nom']] = {
                'Quantite': float(item['Quantite']),
                'Unite': item['Unite'],
                'Prix_Unitaire': float(item['Prix_Unitaire'])
            }
        except ValueError:
            # Ignorer les lignes avec des données non numériques
            continue
    return inventory_dict

def update_inventory_cell(item_name, new_quantity):
    """
    Met à jour la quantité d'un ingrédient dans l'onglet 'Inventaire'.
    Ceci est une opération de recherche et mise à jour (lente, mais fonctionnelle).
    """
    try:
        wks = get_worksheet('Inventaire')
        # Cherche la ligne de l'ingrédient par son nom
        cell = wks.find(item_name, in_column=1) 
        
        # Met à jour la cellule dans la colonne 'Quantite' (Colonne B)
        # Note: B est la deuxième colonne (index 2 dans gspread)
        wks.update_cell(cell.row, 2, new_quantity) 
        return True
    except gspread.CellNotFound:
        # L'ingrédient n'existe pas, on l'ajoute comme une perte si nécessaire
        # Ou on doit ajouter la ligne avant de mettre à jour
        return False
    except Exception as e:
        print(f"Erreur lors de la mise à jour de l'inventaire: {e}")
        return False

# --- LOGIQUE MÉTIER PRINCIPALE (Basée sur l'application précédente) ---

def process_order_logic(dish_name, quantity=1):
    """Traite une commande: vérifie le stock et déduit."""
    if sh is None: return "Erreur de connexion", 500

    inventory_data = load_data('Inventaire')
    recipes_data = load_data('Recettes')
    
    inventory_dict = get_inventory_dict(inventory_data)
    
    # 1. Trouver la recette
    recipe = {}
    for row in recipes_data:
        if row['Plat'] == dish_name:
            recipe[row['Ingredient']] = float(row['Quantite_Req'])
    
    if not recipe:
        return f"Plat '{dish_name}' introuvable.", 404

    # 2. Vérification du stock et calcul du coût
    updates = {}
    cost_of_goods_sold = 0
    missing_items = {}
    
    for item, qty_needed in recipe.items():
        total_needed = qty_needed * quantity
        
        if item not in inventory_dict or inventory_dict[item]['Quantite'] < total_needed:
            missing_items[item] = total_needed - inventory_dict.get(item, {}).get('Quantite', 0)
        
        # Calcul des nouvelles quantités et du coût
        new_qty = inventory_dict[item]['Quantite'] - total_needed
        updates[item] = new_qty
        cost_of_goods_sold += total_needed * inventory_dict[item]['Prix_Unitaire']
        
    if missing_items:
        return f"Stock insuffisant. Manque: {missing_items}", 400

    # 3. Mise à jour du stock (écriture dans la Sheet)
    success = True
    for item, new_qty in updates.items():
        if not update_inventory_cell(item, round(new_qty, 2)):
            success = False
            break

    if not success:
        return "Erreur lors de la mise à jour d'une cellule d'inventaire.", 500

    # 4. Enregistrement de la vente (ajout d'une nouvelle ligne)
    sale_entry = [
        datetime.now().strftime('%Y-%m-%d %H:%M'),
        dish_name,
        quantity,
        round(cost_of_goods_sold, 2)
    ]
    get_worksheet('Commandes').append_row(sale_entry)

    return f"Commande '{dish_name}' x{quantity} traitée. Coût Matière: {round(cost_of_goods_sold, 2)} €.", 200

# --- ROUTES FLASK (API simple pour l'interface) ---

@app.route('/', methods=['GET'])
def home():
    """Affiche un tableau de bord simple et les options."""
    try:
        inventory = load_data('Inventaire')
        recipes = load_data('Recettes')
        
        inventory_html = '<table><tr><th>Ingrédient</th><th>Quantité</th><th>Unité</th></tr>'
        for item in inventory:
            inventory_html += f"<tr><td>{item['Nom']}</td><td>{item['Quantite']}</td><td>{item['Unite']}</td></tr>"
        inventory_html += '</table>'

        # Formulaire simple pour la commande
        form_html = f"""
        <h2>🛒 Traiter une Commande</h2>
        <form method="POST" action="/order">
            <label for="dish">Plat:</label>
            <select name="dish" id="dish">
                {''.join([f'<option value="{r['Plat']}">{r['Plat']}</option>' for r in recipes if 'Plat' in r])}
            </select><br><br>
            <label for="qty">Quantité:</label>
            <input type="number" id="qty" name="qty" value="1" min="1"><br><br>
            <input type="submit" value="Valider la Commande">
        </form>
        """

        return render_template_string(f"<h1>Gestion Stock Restaurant</h1><h2>Inventaire Actuel</h2>{inventory_html}<hr>{form_html}")
    
    except ConnectionError as e:
        return f"<h1>Erreur de connexion à Google Sheets</h1><p>{e}</p>", 500
    except Exception as e:
        return f"Une erreur s'est produite: {e}", 500

@app.route('/order', methods=['POST'])
def handle_order():
    """Route pour traiter une commande via le formulaire."""
    dish = request.form.get('dish')
    qty = int(request.form.get('qty', 1))
    
    message, status_code = process_order_logic(dish, qty)
    
    return jsonify({"message": message}), status_code

@app.route('/loss', methods=['POST'])
def handle_loss():
    """Route API pour enregistrer une perte (peut être appelée par un autre système ou formulaire)."""
    # Exemple de données POST: {"item": "Farine", "quantity": 1.5, "reason": "Erreur"}
    data = request.json
    item = data.get('item')
    quantity = float(data.get('quantity'))
    reason = data.get('reason', 'Non spécifiée')

    # Logique de perte simplifiée pour cette démonstration
    # Dans un vrai système, vous auriez une fonction register_loss_logic(item, quantity, reason)
    try:
        current_inventory = load_data('Inventaire')
        inventory_dict = get_inventory_dict(current_inventory)
        
        if item not in inventory_dict or inventory_dict[item]['Quantite'] < quantity:
            return jsonify({"message": f"Stock insuffisant pour {item}"}), 400

        new_qty = inventory_dict[item]['Quantite'] - quantity
        update_inventory_cell(item, round(new_qty, 2))
        
        # Enregistrement de la perte
        loss_value = quantity * inventory_dict[item]['Prix_Unitaire']
        loss_entry = [
            datetime.now().strftime('%Y-%m-%d %H:%M'),
            item,
            quantity,
            reason,
            round(loss_value, 2)
        ]
        get_worksheet('Pertes').append_row(loss_entry)

        return jsonify({"message": f"Perte de {quantity} de {item} enregistrée (Coût: {loss_value:.2f} €)"}), 200

    except Exception as e:
        return jsonify({"message": f"Erreur de traitement de la perte: {e}"}), 500

if __name__ == '__main__':
    # Ne pas utiliser cette ligne pour Render. Render utilise Gunicorn.
    # Cette ligne est pour les tests locaux.

    app.run(debug=True)
