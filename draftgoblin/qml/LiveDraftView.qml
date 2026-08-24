pragma ComponentBehavior: Bound

import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Item {
    id: root

    required property var sessionState
    required property var recommendationModel
    required property bool narrow
    required property var displayPreferences
    readonly property bool hasRecommendations: sessionState.recommendations
        && sessionState.recommendations.cards
        && sessionState.recommendations.cards.length > 0
    readonly property string draftHeading: {
        const draft = sessionState.draft
        if (!draft)
            return sessionState.status.message
        if (draft.completed)
            return "Draft complete"
        if (draft.pack_number === null)
            return draft.event_name
        return "Pack " + (draft.pack_number + 1)
            + " · Pick " + (draft.pick_number + 1)
    }
    readonly property string emptyHeading: {
        if (sessionState.draft && sessionState.draft.completed)
            return "Draft complete"
        if (sessionState.status.phase === "starting")
            return "Loading live draft data"
        return "Ready for your next Quick Draft"
    }

    readonly property var selectedRecommendation: {
        const recommendations = sessionState.recommendations
        if (!recommendations || !recommendations.cards)
            return null
        const cards = recommendations.cards
        for (let index = 0; index < cards.length; index++) {
            const recommendation = cards[index]
            if (recommendation.card.grp_id === recommendations.selected_grp_id)
                return recommendation
        }
        return null
    }

    property bool recommendationFocusPublishedWhileVisible: false

    onVisibleChanged: {
        if (!root.visible) {
            root.recommendationFocusPublishedWhileVisible = false
            return
        }
        Qt.callLater(function() {
            if (!root.visible || root.recommendationFocusPublishedWhileVisible
                    || !root.selectedRecommendation) {
                return
            }
            root.recommendationFocusPublishedWhileVisible = true
            sessionProvider.chooseRecommendation(
                root.selectedRecommendation.card.grp_id
            )
        })
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: Theme.gutter

        RowLayout {
            Layout.fillWidth: true
            spacing: 12

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2

                Label {
                    text: root.draftHeading
                    color: Theme.text
                    font.pixelSize: 22
                    font.bold: true
                }

                Label {
                    text: root.hasRecommendations
                        ? root.sessionState.recommendations.cards.length
                            + " cards available · Choose a recommendation"
                        : root.sessionState.status.message
                    color: Theme.textMuted
                }
            }

            Label {
                visible: !root.narrow
                text: "RANK BY"
                color: Theme.textMuted
                font.pixelSize: 10
                font.bold: true
            }

            ComboBox {
                id: rankingSelector
                objectName: "rankingSelector"
                Layout.preferredWidth: root.narrow ? 130 : 166
                model: [
                    { key: "score", label: "DG Score" },
                    { key: "win_rate", label: "17L WR" },
                    { key: "alsa", label: "ALSA" },
                    { key: "mana_value", label: "Mana value" }
                ]
                textRole: "label"
                currentIndex: {
                    for (let index = 0; index < model.length; index++)
                        if (model[index].key === root.sessionState.recommendations.ranking_mode)
                            return index
                    return 0
                }
                Accessible.name: "Recommendation ranking"
                onActivated: sessionProvider.changeRanking(model[currentIndex].key)
            }
        }

        StateBanner {
            Layout.fillWidth: true
            sessionState: root.sessionState
        }

        Rectangle {
            visible: !root.hasRecommendations
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: Theme.surfaceLow
            border.color: Theme.outline
            border.width: 1
            radius: Theme.radius

            ColumnLayout {
                anchors.centerIn: parent
                width: Math.min(parent.width - 48, 520)
                spacing: 14

                Label {
                    Layout.fillWidth: true
                    text: root.emptyHeading
                    color: Theme.text
                    font.pixelSize: 22
                    font.bold: true
                    horizontalAlignment: Text.AlignHCenter
                }

                Label {
                    Layout.fillWidth: true
                    text: "Draftgoblin follows Arena automatically and never writes to the game."
                    color: Theme.textMuted
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                }

                Repeater {
                    model: [
                        ["Card metadata", root.sessionState.card_data.message],
                        ["Arena log", root.sessionState.status.message],
                        ["Arena account", root.sessionState.active_account
                            ? root.sessionState.active_account.screen_name
                                || root.sessionState.active_account.account_id
                            : "Not detected"],
                        ["Draft", root.sessionState.draft
                            ? root.sessionState.draft.event_name
                            : "No Quick Draft detected"],
                        ["Ratings", root.sessionState.ratings.message]
                    ]

                    delegate: Rectangle {
                        required property var modelData

                        Layout.fillWidth: true
                        Layout.preferredHeight: 46
                        color: Theme.surface
                        radius: Theme.radius

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 12
                            anchors.rightMargin: 12

                            Label {
                                Layout.preferredWidth: 126
                                text: modelData[0]
                                color: Theme.text
                                font.bold: true
                            }
                            Label {
                                Layout.fillWidth: true
                                text: modelData[1]
                                color: Theme.textMuted
                                elide: Text.ElideRight
                            }
                        }
                    }
                }
            }
        }

        RowLayout {
            visible: !root.narrow && root.hasRecommendations
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: Theme.gutter

            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumWidth: 430
                color: "transparent"

                ColumnLayout {
                    anchors.fill: parent
                    spacing: 6

                    RowLayout {
                        Layout.fillWidth: true
                        Layout.leftMargin: 12
                        Layout.rightMargin: 12

                        Label { Layout.preferredWidth: 26; text: "#"; color: Theme.textMuted; font.pixelSize: 10 }
                        Label { Layout.fillWidth: true; text: "CARD"; color: Theme.textMuted; font.pixelSize: 10 }
                        Label { Layout.preferredWidth: 64; text: "COLOR"; color: Theme.textMuted; font.pixelSize: 10 }
                        Label { Layout.preferredWidth: 52; text: "DG"; color: Theme.textMuted; font.pixelSize: 10; horizontalAlignment: Text.AlignRight }
                        Label { visible: root.displayPreferences.secondaryStats; Layout.preferredWidth: 58; text: "17L WR"; color: Theme.textMuted; font.pixelSize: 10; horizontalAlignment: Text.AlignRight }
                        Label { Layout.preferredWidth: 34; text: "GRADE"; color: Theme.textMuted; font.pixelSize: 10 }
                        Label { Layout.preferredWidth: 84; text: "FIT"; color: Theme.textMuted; font.pixelSize: 10; horizontalAlignment: Text.AlignRight }
                    }

                    ListView {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        spacing: root.displayPreferences.compactDensity ? 3 : 5
                        clip: true
                        model: root.recommendationModel
                        Accessible.name: "Ranked recommendations"

                        delegate: RecommendationRow {
                            required property var modelData

                            width: ListView.view.width
                            recommendation: modelData
                            selected: root.sessionState.recommendations.selected_grp_id === modelData.card.grp_id
                            wide: true
                            secondaryStats: root.displayPreferences.secondaryStats
                            onChosen: grpId => sessionProvider.chooseRecommendation(grpId)
                        }
                    }
                }
            }

            CardPreview {
                visible: root.displayPreferences.cardPreview
                Layout.preferredWidth: 292
                Layout.fillHeight: true
                recommendation: root.selectedRecommendation
                loading: root.sessionState.card_data.phase === "loading"
                imageState: root.sessionState.card_image
            }

            PoolSummaryPanel {
                Layout.preferredWidth: 252
                Layout.fillHeight: true
                pool: root.sessionState.pool
            }
        }

        ColumnLayout {
            visible: root.narrow && root.hasRecommendations
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 8

            ListView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: 260
                spacing: root.displayPreferences.compactDensity ? 3 : 6
                clip: true
                model: root.recommendationModel
                Accessible.name: "Ranked recommendations"

                delegate: RecommendationRow {
                    required property var modelData

                    width: ListView.view.width
                    recommendation: modelData
                    selected: root.sessionState.recommendations.selected_grp_id === modelData.card.grp_id
                    secondaryStats: root.displayPreferences.secondaryStats
                    wide: false
                    onChosen: grpId => sessionProvider.chooseRecommendation(grpId)
                }
            }

            TabBar {
                id: detailTabs
                objectName: "liveDetailTabs"
                Layout.fillWidth: true
                Component.onCompleted: {
                    if (!root.displayPreferences.cardPreview)
                        detailTabs.setCurrentIndex(1)
                }

                TabButton {
                    visible: root.displayPreferences.cardPreview
                    text: "Card details"
                    Accessible.name: "Card details"
                }
                TabButton { text: "Pool"; Accessible.name: "Pool details" }
            }

            Connections {
                target: root.displayPreferences

                function onPreferencesChanged() {
                    if (!root.displayPreferences.cardPreview)
                        detailTabs.setCurrentIndex(1)
                }
            }

            StackLayout {
                Layout.fillWidth: true
                Layout.preferredHeight: 330
                currentIndex: root.displayPreferences.cardPreview ? detailTabs.currentIndex : 1

                CardPreview {
                    objectName: "narrowLiveCardPreview"
                    visible: root.displayPreferences.cardPreview
                    recommendation: root.selectedRecommendation
                    loading: root.sessionState.card_data.phase === "loading"
                    imageState: root.sessionState.card_image
                }

                PoolSummaryPanel {
                    objectName: "narrowLivePoolDetails"
                    pool: root.sessionState.pool
                }
            }
        }
    }
}

