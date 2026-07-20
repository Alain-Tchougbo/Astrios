import { Component } from "react";

/**
 * Filet de securite d'affichage.
 *
 * Sans limite d'erreur, la moindre exception levee pendant un rendu (ex. un champ
 * "details" d'action absent ou malforme, une date inattendue) demonte tout l'arbre
 * React : l'utilisateur se retrouve devant une page entierement blanche, sans
 * message et sans moyen de repartir autrement qu'en rechargeant a l'aveugle.
 *
 * On affiche a la place un message lisible et un bouton de rechargement. L'erreur
 * reste envoyee dans la console pour le debogage.
 */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error("Erreur de rendu non rattrapee :", error, info);
  }

  render() {
    if (!this.state.error) return this.props.children;

    return (
      <div className="app-error" role="alert" style={{ margin: 24, padding: 16 }}>
        <strong>Une erreur est survenue dans l'affichage.</strong>
        <p style={{ marginTop: 8 }}>
          L'application n'a pas pu afficher cette vue. Rechargez la page pour repartir sur un
          etat sain — vos missions sont enregistrees cote serveur, rien n'est perdu.
        </p>
        <button type="button" style={{ marginTop: 12 }} onClick={() => window.location.reload()}>
          Recharger la page
        </button>
      </div>
    );
  }
}
